"""
Async SQLite persistence layer
=============================

* Every emergency is identified by a UUID ``emergency_id`` string.  Events,
  locations and audio segments all reference that same id, so it is impossible
  to attach data to the wrong emergency.
* A partial unique index enforces **one active emergency per user** at the
  database level (race-safe, not just an application check).
* ``init_db()`` also performs a safe in-place migration of the original
  prototype schema (integer emergency ids) so existing data is preserved.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, List, Optional

import aiosqlite

from config import settings

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 2


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActiveEmergencyExists(Exception):
    """Raised when a user already has an active emergency."""

    def __init__(self, emergency_id: str):
        super().__init__(f"user already has an active emergency ({emergency_id})")
        self.emergency_id = emergency_id


# ──────────────────────────────────────────────────────────────────────
# Connection helpers
# ──────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    db = await aiosqlite.connect(settings.db_path)
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA foreign_keys = ON")
    await db.execute("PRAGMA busy_timeout = 5000")
    try:
        yield db
    finally:
        await db.close()


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS devices (
        device_id      TEXT PRIMARY KEY,
        user_id        TEXT NOT NULL,
        display_name   TEXT NOT NULL DEFAULT '',
        platform       TEXT NOT NULL DEFAULT 'android',
        manufacturer   TEXT NOT NULL DEFAULT '',
        model          TEXT NOT NULL DEFAULT '',
        os_version     TEXT NOT NULL DEFAULT '',
        app_version    TEXT NOT NULL DEFAULT '',
        capabilities   TEXT NOT NULL DEFAULT '{}',
        token_hash     TEXT NOT NULL UNIQUE,
        created_at     TEXT NOT NULL,
        last_seen_at   TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_devices_user ON devices(user_id)",
    """
    CREATE TABLE IF NOT EXISTS emergencies (
        emergency_id       TEXT PRIMARY KEY,
        user_id            TEXT NOT NULL,
        device_id          TEXT,
        status             TEXT NOT NULL,
        trigger_type       TEXT NOT NULL,
        trigger_metadata   TEXT NOT NULL DEFAULT '{}',
        latitude           REAL,
        longitude          REAL,
        digipin            TEXT,
        digipin_available  INTEGER NOT NULL DEFAULT 1,
        location_accuracy_m REAL,
        location_provider  TEXT,
        telemetry          TEXT NOT NULL DEFAULT '{}',
        client_emergency_id TEXT,
        created_at         TEXT NOT NULL,
        activated_at       TEXT,
        last_update_at     TEXT NOT NULL,
        ended_at           TEXT,
        ended_by           TEXT,
        resolution_note    TEXT,
        cancelled_at       TEXT,
        cancel_reason      TEXT,
        FOREIGN KEY (device_id) REFERENCES devices(device_id)
    )
    """,
    # One active emergency per user — enforced by the database itself.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_per_user
        ON emergencies(user_id) WHERE status IN ('emergency', 'verifying')
    """,
    "CREATE INDEX IF NOT EXISTS idx_emergencies_status ON emergencies(status)",
    "CREATE INDEX IF NOT EXISTS idx_emergencies_created ON emergencies(created_at DESC)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_emergencies_client_id
        ON emergencies(client_emergency_id) WHERE client_emergency_id IS NOT NULL
    """,
    """
    CREATE TABLE IF NOT EXISTS emergency_events (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        emergency_id TEXT NOT NULL,
        event_type   TEXT NOT NULL,
        description  TEXT NOT NULL,
        payload      TEXT NOT NULL DEFAULT '{}',
        source       TEXT NOT NULL DEFAULT 'device',
        timestamp    TEXT NOT NULL,
        FOREIGN KEY (emergency_id) REFERENCES emergencies(emergency_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_events_emergency ON emergency_events(emergency_id, timestamp)",
    """
    CREATE TABLE IF NOT EXISTS emergency_locations (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        emergency_id       TEXT NOT NULL,
        latitude           REAL NOT NULL,
        longitude          REAL NOT NULL,
        digipin            TEXT,
        digipin_available  INTEGER NOT NULL DEFAULT 1,
        accuracy_m         REAL,
        altitude_m         REAL,
        speed_mps          REAL,
        heading_deg        REAL,
        provider           TEXT,
        is_mock            INTEGER NOT NULL DEFAULT 0,
        timestamp          TEXT NOT NULL,
        FOREIGN KEY (emergency_id) REFERENCES emergencies(emergency_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_locations_emergency ON emergency_locations(emergency_id, timestamp)",
    """
    CREATE TABLE IF NOT EXISTS audio_segments (
        segment_id       TEXT PRIMARY KEY,
        emergency_id     TEXT NOT NULL,
        sequence         INTEGER NOT NULL,
        file_name        TEXT NOT NULL,
        file_path        TEXT NOT NULL,
        mime_type        TEXT NOT NULL,
        size_bytes       INTEGER NOT NULL,
        duration_seconds REAL,
        sample_rate      INTEGER,
        channels         INTEGER,
        codec            TEXT,
        started_at       TEXT,
        ended_at         TEXT,
        created_at       TEXT NOT NULL,
        FOREIGN KEY (emergency_id) REFERENCES emergencies(emergency_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_segments_sequence
        ON audio_segments(emergency_id, sequence)
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    TEXT NOT NULL UNIQUE,
        pin_hash   TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS emergency_contacts (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id      TEXT NOT NULL,
        name         TEXT NOT NULL,
        phone_number TEXT NOT NULL,
        relationship TEXT DEFAULT '',
        created_at   TEXT NOT NULL
    )
    """,
]


async def init_db() -> dict:
    """Create the schema (idempotent) and migrate legacy data when present.

    The legacy migration runs **first**: the current index definitions reference
    columns that the original prototype schema does not have, so the legacy
    tables must be renamed before any CREATE INDEX runs.
    """
    from migrations import migrate_legacy_if_needed  # local import: optional dep

    os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute("PRAGMA journal_mode = WAL")
        await db.commit()

    summary = {"schema_version": SCHEMA_VERSION, "migrated": None}
    try:
        summary["migrated"] = await migrate_legacy_if_needed()
    except Exception as exc:  # pragma: no cover - migration must never block startup
        logger.error("Legacy migration failed: %s", exc, exc_info=True)

    async with _connect() as db:
        for statement in SCHEMA_STATEMENTS:
            try:
                await db.execute(statement)
            except sqlite3.OperationalError as exc:
                logger.error("Schema statement failed (%s): %s", exc, statement.strip()[:80])
                raise
        await db.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        await db.commit()

    return summary


# ──────────────────────────────────────────────────────────────────────
# Devices
# ──────────────────────────────────────────────────────────────────────

async def register_device(data: Dict[str, Any], token_hash: str) -> Dict[str, Any]:
    device_id = str(uuid.uuid4())
    now = utcnow()
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO devices (device_id, user_id, display_name, platform, manufacturer,
                                 model, os_version, app_version, capabilities, token_hash,
                                 created_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                device_id,
                data["user_id"],
                data.get("display_name", ""),
                data.get("platform", "android"),
                data.get("manufacturer", ""),
                data.get("model", ""),
                data.get("os_version", ""),
                data.get("app_version", ""),
                json.dumps(data.get("capabilities", {})),
                token_hash,
                now,
                now,
            ),
        )
        await db.commit()
    return {"device_id": device_id, "user_id": data["user_id"]}


async def get_device_by_token(token: str) -> Optional[Dict[str, Any]]:
    from security import hash_token

    async with _connect() as db:
        cursor = await db.execute(
            "SELECT * FROM devices WHERE token_hash = ?", (hash_token(token),)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        await db.execute(
            "UPDATE devices SET last_seen_at = ? WHERE device_id = ?",
            (utcnow(), row["device_id"]),
        )
        await db.commit()
        return _device_dict(row)


async def get_device(device_id: str) -> Optional[Dict[str, Any]]:
    async with _connect() as db:
        cursor = await db.execute("SELECT * FROM devices WHERE device_id = ?", (device_id,))
        row = await cursor.fetchone()
        return _device_dict(row) if row else None


async def list_devices(user_id: Optional[str] = None) -> List[Dict[str, Any]]:
    query = "SELECT * FROM devices"
    params: tuple = ()
    if user_id:
        query += " WHERE user_id = ?"
        params = (user_id,)
    query += " ORDER BY last_seen_at DESC"
    async with _connect() as db:
        cursor = await db.execute(query, params)
        return [_device_dict(r) for r in await cursor.fetchall()]


def _device_dict(row: aiosqlite.Row) -> Dict[str, Any]:
    data = dict(row)
    data["capabilities"] = _json_or(data.get("capabilities"), {})
    data.pop("token_hash", None)  # never expose token hashes
    return data


# ──────────────────────────────────────────────────────────────────────
# Emergencies
# ──────────────────────────────────────────────────────────────────────

async def find_active_emergency(user_id: str) -> Optional[Dict[str, Any]]:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT * FROM emergencies WHERE user_id = ? AND status IN ('emergency','verifying') "
            "ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )
        row = await cursor.fetchone()
        return _emergency_dict(row) if row else None


async def create_emergency(data: Dict[str, Any]) -> Dict[str, Any]:
    """Insert a new active emergency. Raises ActiveEmergencyExists on conflict."""
    emergency_id = data.get("emergency_id") or str(uuid.uuid4())
    now = utcnow()
    async with _connect() as db:
        try:
            await db.execute(
                """
                INSERT INTO emergencies (
                    emergency_id, user_id, device_id, status, trigger_type, trigger_metadata,
                    latitude, longitude, digipin, digipin_available, location_accuracy_m,
                    location_provider, telemetry, client_emergency_id, created_at, activated_at,
                    last_update_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    emergency_id,
                    data["user_id"],
                    data.get("device_id"),
                    data.get("status", "emergency"),
                    data["trigger_type"],
                    json.dumps(data.get("trigger_metadata", {})),
                    data.get("latitude"),
                    data.get("longitude"),
                    data.get("digipin"),
                    1 if data.get("digipin") else 0,
                    data.get("accuracy_m"),
                    data.get("location_provider"),
                    json.dumps(data.get("telemetry", {})),
                    data.get("client_emergency_id"),
                    data.get("created_at") or now,
                    data.get("activated_at") or now,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            existing = await find_active_emergency(data["user_id"])
            if existing:
                raise ActiveEmergencyExists(existing["emergency_id"]) from exc
            # client_emergency_id uniqueness → idempotent retry
            if data.get("client_emergency_id"):
                row = await find_by_client_id(data["client_emergency_id"])
                if row:
                    return row
            raise

        # Seed the location track and the initial event trail
        if data.get("latitude") is not None and data.get("longitude") is not None:
            await db.execute(
                """
                INSERT INTO emergency_locations (
                    emergency_id, latitude, longitude, digipin, digipin_available,
                    accuracy_m, provider, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    emergency_id,
                    data["latitude"],
                    data["longitude"],
                    data.get("digipin"),
                    1 if data.get("digipin") else 0,
                    data.get("accuracy_m"),
                    data.get("location_provider"),
                    data.get("location_timestamp") or now,
                ),
            )
        await db.commit()
        return await get_emergency(emergency_id)  # type: ignore[return-value]


async def find_by_client_id(client_emergency_id: str) -> Optional[Dict[str, Any]]:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT * FROM emergencies WHERE client_emergency_id = ?", (client_emergency_id,)
        )
        row = await cursor.fetchone()
        return _emergency_dict(row) if row else None


async def get_emergency(emergency_id: str) -> Optional[Dict[str, Any]]:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT * FROM emergencies WHERE emergency_id = ?", (emergency_id,)
        )
        row = await cursor.fetchone()
        if not row:
            return None
        record = _emergency_dict(row)
        if record.get("device_id"):
            dev = await db.execute(
                "SELECT * FROM devices WHERE device_id = ?", (record["device_id"],)
            )
            device_row = await dev.fetchone()
            record["device"] = _device_dict(device_row) if device_row else None
        else:
            record["device"] = None
        return record


async def list_emergencies(
    status: Optional[str] = None,
    user_id: Optional[str] = None,
    limit: int = 100,
    active_only: bool = False,
) -> List[Dict[str, Any]]:
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)
    if active_only:
        clauses.append("status IN ('emergency','verifying')")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    async with _connect() as db:
        cursor = await db.execute(
            f"SELECT * FROM emergencies {where} ORDER BY created_at DESC LIMIT ?",
            (*params, limit),
        )
        rows = await cursor.fetchall()
        results = []
        for row in rows:
            record = _emergency_dict(row)
            emergency_id = record["emergency_id"]

            loc_cursor = await db.execute(
                "SELECT * FROM emergency_locations WHERE emergency_id = ? "
                "ORDER BY timestamp DESC, id DESC LIMIT 1",
                (emergency_id,),
            )
            loc = await loc_cursor.fetchone()
            record["latest_location"] = _location_dict(loc) if loc else None

            count_cursor = await db.execute(
                "SELECT COUNT(*) AS events, MAX(timestamp) AS last_event FROM emergency_events "
                "WHERE emergency_id = ?",
                (emergency_id,),
            )
            counts = await count_cursor.fetchone()
            record["event_count"] = counts["events"] if counts else 0
            record["last_event_at"] = counts["last_event"] if counts else None

            seg_cursor = await db.execute(
                "SELECT COUNT(*) AS segments, COALESCE(SUM(size_bytes),0) AS bytes, "
                "COALESCE(SUM(duration_seconds),0) AS duration FROM audio_segments "
                "WHERE emergency_id = ?",
                (emergency_id,),
            )
            seg = await seg_cursor.fetchone()
            record["audio_segment_count"] = seg["segments"] if seg else 0
            record["audio_total_bytes"] = seg["bytes"] if seg else 0
            record["audio_total_seconds"] = seg["duration"] if seg else 0
            record["has_audio"] = bool(seg and seg["segments"] > 0)

            if record.get("device_id"):
                dev = await db.execute(
                    "SELECT * FROM devices WHERE device_id = ?", (record["device_id"],)
                )
                device_row = await dev.fetchone()
                record["device"] = _device_dict(device_row) if device_row else None
            else:
                record["device"] = None

            results.append(record)
        return results


async def get_emergency_bundle(emergency_id: str, event_limit: int = 500) -> Optional[Dict[str, Any]]:
    """Full detail view: emergency + device + events + location track + segments."""
    record = await get_emergency(emergency_id)
    if not record:
        return None
    async with _connect() as db:
        ev = await db.execute(
            "SELECT * FROM emergency_events WHERE emergency_id = ? "
            "ORDER BY timestamp ASC, id ASC LIMIT ?",
            (emergency_id, event_limit),
        )
        events = [dict(r) for r in await ev.fetchall()]
        for ev_row in events:
            ev_row["payload"] = _json_or(ev_row.get("payload"), {})

        loc = await db.execute(
            "SELECT * FROM emergency_locations WHERE emergency_id = ? "
            "ORDER BY timestamp ASC, id ASC LIMIT 2000",
            (emergency_id,),
        )
        locations = [_location_dict(r) for r in await loc.fetchall()]

        seg = await db.execute(
            "SELECT * FROM audio_segments WHERE emergency_id = ? ORDER BY sequence ASC",
            (emergency_id,),
        )
        segments = [_segment_dict(r) for r in await seg.fetchall()]

    record["events"] = events
    record["locations"] = locations
    record["audio_segments"] = segments
    record["has_audio"] = bool(segments)
    return record


async def touch_emergency(emergency_id: str) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE emergencies SET last_update_at = ? WHERE emergency_id = ?",
            (utcnow(), emergency_id),
        )
        await db.commit()


async def update_emergency_location(emergency_id: str, data: Dict[str, Any], digipin: Optional[str]) -> Dict[str, Any]:
    """Append a location sample to the track and refresh the emergency headline."""
    now = utcnow()
    timestamp = data.get("timestamp") or now
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO emergency_locations (
                emergency_id, latitude, longitude, digipin, digipin_available, accuracy_m,
                altitude_m, speed_mps, heading_deg, provider, is_mock, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                emergency_id,
                data["latitude"],
                data["longitude"],
                digipin,
                1 if digipin else 0,
                data.get("accuracy_m"),
                data.get("altitude_m"),
                data.get("speed_mps"),
                data.get("heading_deg"),
                data.get("provider"),
                1 if data.get("is_mock") else 0,
                timestamp,
            ),
        )
        await db.execute(
            """
            UPDATE emergencies
               SET latitude = ?, longitude = ?, digipin = ?, digipin_available = ?,
                   location_accuracy_m = ?, location_provider = ?, last_update_at = ?
             WHERE emergency_id = ?
            """,
            (
                data["latitude"],
                data["longitude"],
                digipin,
                1 if digipin else 0,
                data.get("accuracy_m"),
                data.get("provider"),
                now,
                emergency_id,
            ),
        )
        await db.commit()
    return {"timestamp": timestamp, "digipin": digipin}


async def set_telemetry(emergency_id: str, telemetry: Dict[str, Any]) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE emergencies SET telemetry = ?, last_update_at = ? WHERE emergency_id = ?",
            (json.dumps(telemetry), utcnow(), emergency_id),
        )
        await db.commit()


async def end_emergency(
    emergency_id: str,
    status: str,
    ended_by: str,
    reason: Optional[str] = None,
    note: Optional[str] = None,
) -> bool:
    """Mark an emergency cancelled (never became visible) or resolved (ended)."""
    now = utcnow()
    async with _connect() as db:
        if status == "cancelled":
            cursor = await db.execute(
                """
                UPDATE emergencies
                   SET status = ?, ended_at = ?, cancelled_at = ?, cancel_reason = ?,
                       ended_by = ?, resolution_note = ?, last_update_at = ?
                 WHERE emergency_id = ?
                """,
                (status, now, now, reason, ended_by, note, now, emergency_id),
            )
        else:
            cursor = await db.execute(
                """
                UPDATE emergencies
                   SET status = ?, ended_at = ?, ended_by = ?, resolution_note = ?,
                       last_update_at = ?
                 WHERE emergency_id = ? AND status IN ('emergency','verifying')
                """,
                (status, now, ended_by, note, now, emergency_id),
            )
        changed = cursor.rowcount > 0
        if changed:
            await db.execute(
                """
                INSERT INTO emergency_events (emergency_id, event_type, description, payload, source, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    emergency_id,
                    "emergency_cancelled" if status == "cancelled" else "emergency_resolved",
                    f"Emergency {status} by {ended_by}"
                    + (f" — {note}" if note else "")
                    + (f" ({reason})" if reason else ""),
                    json.dumps({"ended_by": ended_by, "reason": reason, "note": note}),
                    "backend",
                    now,
                ),
            )
        await db.commit()
        return changed


# ──────────────────────────────────────────────────────────────────────
# Events / locations
# ──────────────────────────────────────────────────────────────────────

async def add_event(
    emergency_id: str,
    event_type: str,
    description: str,
    payload: Optional[Dict[str, Any]] = None,
    source: str = "device",
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    now = utcnow()
    ts = timestamp or now
    async with _connect() as db:
        cursor = await db.execute(
            """
            INSERT INTO emergency_events (emergency_id, event_type, description, payload, source, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (emergency_id, event_type, description, json.dumps(payload or {}), source, ts),
        )
        await db.execute(
            "UPDATE emergencies SET last_update_at = ? WHERE emergency_id = ?",
            (now, emergency_id),
        )
        await db.commit()
        return {
            "id": cursor.lastrowid,
            "emergency_id": emergency_id,
            "event_type": event_type,
            "description": description,
            "payload": payload or {},
            "source": source,
            "timestamp": ts,
        }


async def get_events(emergency_id: str, limit: int = 500) -> List[Dict[str, Any]]:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT * FROM emergency_events WHERE emergency_id = ? "
            "ORDER BY timestamp ASC, id ASC LIMIT ?",
            (emergency_id, limit),
        )
        rows = [dict(r) for r in await cursor.fetchall()]
    for row in rows:
        row["payload"] = _json_or(row.get("payload"), {})
    return rows


async def get_locations(emergency_id: str, limit: int = 2000) -> List[Dict[str, Any]]:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT * FROM emergency_locations WHERE emergency_id = ? "
            "ORDER BY timestamp ASC, id ASC LIMIT ?",
            (emergency_id, limit),
        )
        return [_location_dict(r) for r in await cursor.fetchall()]


# ──────────────────────────────────────────────────────────────────────
# Audio segments
# ──────────────────────────────────────────────────────────────────────

async def add_audio_segment(data: Dict[str, Any]) -> Dict[str, Any]:
    segment_id = data.get("segment_id") or str(uuid.uuid4())
    now = utcnow()
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO audio_segments (
                segment_id, emergency_id, sequence, file_name, file_path, mime_type,
                size_bytes, duration_seconds, sample_rate, channels, codec,
                started_at, ended_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(emergency_id, sequence) DO UPDATE SET
                file_name = excluded.file_name,
                file_path = excluded.file_path,
                mime_type = excluded.mime_type,
                size_bytes = excluded.size_bytes,
                duration_seconds = excluded.duration_seconds,
                sample_rate = excluded.sample_rate,
                channels = excluded.channels,
                codec = excluded.codec,
                started_at = excluded.started_at,
                ended_at = excluded.ended_at
            """,
            (
                segment_id,
                data["emergency_id"],
                data["sequence"],
                data["file_name"],
                data["file_path"],
                data.get("mime_type", "audio/wav"),
                data.get("size_bytes", 0),
                data.get("duration_seconds"),
                data.get("sample_rate"),
                data.get("channels"),
                data.get("codec"),
                data.get("started_at"),
                data.get("ended_at"),
                now,
            ),
        )
        await db.execute(
            "UPDATE emergencies SET last_update_at = ? WHERE emergency_id = ?",
            (now, data["emergency_id"]),
        )
        await db.commit()

    async with _connect() as db:
        cursor = await db.execute(
            "SELECT * FROM audio_segments WHERE emergency_id = ? AND sequence = ?",
            (data["emergency_id"], data["sequence"]),
        )
        row = await cursor.fetchone()
    return _segment_dict(row) if row else {}


async def list_audio_segments(emergency_id: str) -> List[Dict[str, Any]]:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT * FROM audio_segments WHERE emergency_id = ? ORDER BY sequence ASC",
            (emergency_id,),
        )
        return [_segment_dict(r) for r in await cursor.fetchall()]


async def get_audio_segment(emergency_id: str, segment_id: str) -> Optional[Dict[str, Any]]:
    """Segment lookup is always scoped by emergency_id to prevent cross-playback."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT * FROM audio_segments WHERE emergency_id = ? AND segment_id = ?",
            (emergency_id, segment_id),
        )
        row = await cursor.fetchone()
        if not row:
            # also allow addressing by sequence number (used by older clients)
            cursor = await db.execute(
                "SELECT * FROM audio_segments WHERE emergency_id = ? AND CAST(sequence AS TEXT) = ?",
                (emergency_id, segment_id),
            )
            row = await cursor.fetchone()
        return _segment_dict(row) if row else None


async def get_audio_segment_record(emergency_id: str, segment_id: str) -> Optional[Dict[str, Any]]:
    """Raw segment row (including the on-disk path) for the streaming endpoint."""
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT * FROM audio_segments WHERE emergency_id = ? AND segment_id = ?",
            (emergency_id, segment_id),
        )
        row = await cursor.fetchone()
        if not row:
            cursor = await db.execute(
                "SELECT * FROM audio_segments WHERE emergency_id = ? AND CAST(sequence AS TEXT) = ?",
                (emergency_id, segment_id),
            )
            row = await cursor.fetchone()
        return dict(row) if row else None


async def next_audio_sequence(emergency_id: str) -> int:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT COALESCE(MAX(sequence), 0) AS seq FROM audio_segments WHERE emergency_id = ?",
            (emergency_id,),
        )
        row = await cursor.fetchone()
        return int(row["seq"]) + 1 if row else 1


# ──────────────────────────────────────────────────────────────────────
# Stats
# ──────────────────────────────────────────────────────────────────────

async def dashboard_stats() -> Dict[str, Any]:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT status, COUNT(*) AS cnt FROM emergencies GROUP BY status"
        )
        by_status = {r["status"]: r["cnt"] for r in await cursor.fetchall()}
        dev = await db.execute("SELECT COUNT(*) AS cnt FROM devices")
        devices = (await dev.fetchone())["cnt"]
        seg = await db.execute(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(size_bytes),0) AS bytes FROM audio_segments"
        )
        seg_row = await seg.fetchone()
    return {
        "by_status": by_status,
        "active": by_status.get("emergency", 0),
        "verifying": by_status.get("verifying", 0),
        "cancelled": by_status.get("cancelled", 0),
        "resolved": by_status.get("resolved", 0),
        "total": sum(by_status.values()),
        "devices": devices,
        "audio_segments": seg_row["cnt"] if seg_row else 0,
        "audio_bytes": seg_row["bytes"] if seg_row else 0,
    }


# ──────────────────────────────────────────────────────────────────────
# Users / PINs (salted hashes + transparent upgrade of legacy hashes)
# ──────────────────────────────────────────────────────────────────────

async def set_user_pin(user_id: str, pin: str) -> None:
    import hashlib

    from security import hash_pin

    pin_hash = hash_pin(pin)
    now = utcnow()
    async with _connect() as db:
        cursor = await db.execute("SELECT id FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            await db.execute(
                "UPDATE users SET pin_hash = ?, updated_at = ? WHERE user_id = ?",
                (pin_hash, now, user_id),
            )
        else:
            await db.execute(
                "INSERT INTO users (user_id, pin_hash, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (user_id, pin_hash, now, now),
            )
        await db.commit()


async def verify_user_pin(user_id: str, pin: str) -> bool:
    import hashlib

    from security import hash_pin, verify_pin_hash

    async with _connect() as db:
        cursor = await db.execute("SELECT pin_hash FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if not row:
            return False
        stored = row["pin_hash"]
        if verify_pin_hash(pin, stored):
            return True
        # Legacy unsalted SHA-256 hashes (from the original prototype) are
        # accepted once and transparently re-hashed with the salted scheme.
        legacy = hashlib.sha256(pin.encode()).hexdigest()
        if legacy == stored:
            await db.execute(
                "UPDATE users SET pin_hash = ?, updated_at = ? WHERE user_id = ?",
                (hash_pin(pin), utcnow(), user_id),
            )
            await db.commit()
            return True
        return False


async def user_has_pin(user_id: str) -> bool:
    async with _connect() as db:
        cursor = await db.execute("SELECT id FROM users WHERE user_id = ?", (user_id,))
        return await cursor.fetchone() is not None


async def get_user(user_id: str) -> Optional[Dict[str, Any]]:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT user_id, created_at, updated_at FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


# ──────────────────────────────────────────────────────────────────────
# Emergency contacts
# ──────────────────────────────────────────────────────────────────────

async def add_contact(user_id: str, name: str, phone_number: str, relationship: str = "") -> int:
    async with _connect() as db:
        cursor = await db.execute(
            """
            INSERT INTO emergency_contacts (user_id, name, phone_number, relationship, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, name, phone_number, relationship, utcnow()),
        )
        await db.commit()
        return cursor.lastrowid


async def remove_contact(contact_id: int, user_id: str) -> bool:
    async with _connect() as db:
        cursor = await db.execute(
            "DELETE FROM emergency_contacts WHERE id = ? AND user_id = ?",
            (contact_id, user_id),
        )
        await db.commit()
        return cursor.rowcount > 0


async def get_contacts(user_id: str) -> List[Dict[str, Any]]:
    async with _connect() as db:
        cursor = await db.execute(
            "SELECT * FROM emergency_contacts WHERE user_id = ? ORDER BY created_at ASC",
            (user_id,),
        )
        return [dict(r) for r in await cursor.fetchall()]


# ──────────────────────────────────────────────────────────────────────
# Row helpers
# ──────────────────────────────────────────────────────────────────────

def _json_or(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _emergency_dict(row: aiosqlite.Row) -> Dict[str, Any]:
    data = dict(row)
    data["trigger_metadata"] = _json_or(data.get("trigger_metadata"), {})
    data["telemetry"] = _json_or(data.get("telemetry"), {})
    data["digipin_available"] = bool(data.get("digipin_available", 1))
    return data


def _location_dict(row: aiosqlite.Row) -> Dict[str, Any]:
    data = dict(row)
    data["digipin_available"] = bool(data.get("digipin_available", 1))
    data["is_mock"] = bool(data.get("is_mock", 0))
    return data


def _segment_dict(row: aiosqlite.Row) -> Dict[str, Any]:
    data = dict(row)
    # ``file_path`` stays server-side; the API exposes segment ids instead.
    data.pop("file_path", None)
    return data
