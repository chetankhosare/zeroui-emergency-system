"""
In-place migration of the original prototype schema
===================================================

The first version of Zero UI used integer emergency ids (``emergencies.id``).
The current schema keys everything by a UUID ``emergency_id``.  This module
detects the legacy layout and migrates it **without losing data**:

* legacy tables are renamed to ``legacy_*`` (kept as a backup, never dropped),
* every legacy emergency keeps its events, location track and audio files,
* status values are mapped (``safe`` → ``cancelled``),
* because the new schema allows only one *active* emergency per user, older
  legacy rows that are still active are demoted to ``resolved``.

The migration is idempotent: it runs at most once per database file.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import aiosqlite

from config import settings

logger = logging.getLogger(__name__)

LEGACY_TABLES = {
    "emergencies": "legacy_emergencies",
    "events": "legacy_events",
    "locations": "legacy_locations",
    "audio_files": "legacy_audio_files",
}

STATUS_MAP = {
    "emergency": "emergency",
    "verifying": "cancelled",
    "safe": "cancelled",
    "resolved": "resolved",
    "cancelled": "cancelled",
}


async def _table_columns(db: aiosqlite.Connection, table: str) -> list[str]:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in await cursor.fetchall()]


async def _table_exists(db: aiosqlite.Connection, table: str) -> bool:
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    )
    return await cursor.fetchone() is not None


async def migrate_legacy_if_needed() -> Optional[Dict[str, Any]]:
    """Returns a summary dict when a migration ran, else ``None``."""
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row

        if not await _table_exists(db, "emergencies"):
            return None

        columns = await _table_columns(db, "emergencies")
        if "emergency_id" in columns:
            return None  # already on the new schema
        if "id" not in columns:
            return None

        logger.warning("Legacy (integer-id) schema detected — migrating in place…")

        # 1. Move legacy tables aside (idempotent: drop any stale backup first)
        for legacy, backup in LEGACY_TABLES.items():
            if not await _table_exists(db, legacy):
                continue
            if await _table_exists(db, backup):
                await db.execute(f"DROP TABLE {backup}")
            await db.execute(f"ALTER TABLE {legacy} RENAME TO {backup}")
        await db.commit()

    # 2. Recreate the current schema
    from database import SCHEMA_STATEMENTS, utcnow

    async with aiosqlite.connect(settings.db_path) as db:
        for statement in SCHEMA_STATEMENTS:
            await db.execute(statement)
        await db.commit()

    # 3. Copy the data across
    summary = {"emergencies": 0, "events": 0, "locations": 0, "audio_segments": 0, "demoted": 0}
    id_map: Dict[int, str] = {}
    active_users: set[str] = set()

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row

        cursor = await db.execute(
            "SELECT * FROM legacy_emergencies ORDER BY created_at DESC"
        )
        legacy_rows = await cursor.fetchall()

        for row in legacy_rows:
            legacy_id = row["id"]
            emergency_id = str(uuid.uuid4())
            id_map[legacy_id] = emergency_id

            status = STATUS_MAP.get((row["status"] or "resolved").lower(), "resolved")
            if status == "emergency":
                if row["user_id"] in active_users:
                    status = "resolved"
                    summary["demoted"] += 1
                else:
                    active_users.add(row["user_id"])

            created_at = row["created_at"] or utcnow()
            resolved_at = row["resolved_at"] if "resolved_at" in row.keys() else None
            try:
                digipin = row["digipin"]
            except (KeyError, IndexError):
                digipin = None

            await db.execute(
                """
                INSERT INTO emergencies (
                    emergency_id, user_id, device_id, status, trigger_type, trigger_metadata,
                    latitude, longitude, digipin, digipin_available, location_accuracy_m,
                    location_provider, telemetry, client_emergency_id, created_at,
                    activated_at, last_update_at, ended_at, ended_by, resolution_note
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, '{}', NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    emergency_id,
                    row["user_id"],
                    status,
                    row["trigger_type"] or "fall",
                    json.dumps({"migrated_from": f"legacy:{legacy_id}"}),
                    row["latitude"],
                    row["longitude"],
                    digipin,
                    1 if digipin else 0,
                    created_at,
                    created_at,
                    resolved_at or created_at,
                    resolved_at,
                    "legacy-import",
                    "Imported from the original prototype schema",
                ),
            )
            summary["emergencies"] += 1

            # Location track
            if await _table_exists(db, "legacy_locations"):
                loc_cursor = await db.execute(
                    "SELECT * FROM legacy_locations WHERE emergency_id = ? ORDER BY timestamp ASC",
                    (legacy_id,),
                )
                for loc in await loc_cursor.fetchall():
                    await db.execute(
                        """
                        INSERT INTO emergency_locations (
                            emergency_id, latitude, longitude, digipin, digipin_available,
                            accuracy_m, provider, is_mock, timestamp
                        ) VALUES (?, ?, ?, ?, ?, NULL, 'legacy', 0, ?)
                        """,
                        (
                            emergency_id,
                            loc["latitude"],
                            loc["longitude"],
                            loc["digipin"],
                            1 if loc["digipin"] else 0,
                            loc["timestamp"] or created_at,
                        ),
                    )
                    summary["locations"] += 1

            # Events
            if await _table_exists(db, "legacy_events"):
                ev_cursor = await db.execute(
                    "SELECT * FROM legacy_events WHERE emergency_id = ? ORDER BY timestamp ASC",
                    (legacy_id,),
                )
                for ev in await ev_cursor.fetchall():
                    await db.execute(
                        """
                        INSERT INTO emergency_events (
                            emergency_id, event_type, description, payload, source, timestamp
                        ) VALUES (?, ?, ?, '{}', 'legacy', ?)
                        """,
                        (emergency_id, ev["event_type"], ev["description"], ev["timestamp"] or created_at),
                    )
                    summary["events"] += 1

            # Audio files
            if await _table_exists(db, "legacy_audio_files"):
                au_cursor = await db.execute(
                    "SELECT * FROM legacy_audio_files WHERE emergency_id = ? ORDER BY timestamp ASC",
                    (legacy_id,),
                )
                sequence = 0
                for au in await au_cursor.fetchall():
                    path = au["file_path"]
                    if not path or not os.path.exists(path):
                        logger.warning("Legacy audio file missing, skipping: %s", path)
                        continue
                    sequence += 1
                    size = os.path.getsize(path)
                    await db.execute(
                        """
                        INSERT INTO audio_segments (
                            segment_id, emergency_id, sequence, file_name, file_path,
                            mime_type, size_bytes, duration_seconds, codec,
                            started_at, ended_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            emergency_id,
                            sequence,
                            os.path.basename(path),
                            path,
                            "audio/webm",
                            size,
                            "opus",
                            au["timestamp"] or created_at,
                            created_at,
                        ),
                    )
                    summary["audio_segments"] += 1

        await db.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('legacy_migrated_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (datetime.now(timezone.utc).isoformat(),),
        )
        await db.commit()

    logger.warning("Legacy migration complete: %s", summary)
    return summary
