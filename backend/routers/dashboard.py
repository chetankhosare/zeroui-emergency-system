"""
Authority dashboard API
=======================

All routes require a dashboard session (JWT) issued by
``POST /api/auth/dashboard/login``; an unrelated browser session cannot read
any user or emergency data.

Every audio route is scoped by ``emergency_id`` **and** ``segment_id`` so audio
can never be mixed between emergencies.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

import database
import notify
from config import settings
from digipin_service import digipin_status
from models import ResolveRequest
from realtime import manager
from security import get_authority
from serializers import (
    serialize_device,
    serialize_detail,
    serialize_segment,
    serialize_summary,
)
from streaming import stream_file

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(get_authority)],
)

EMERGENCY_ID_RE = None  # validated by database lookups


async def _detail(emergency_id: str) -> dict:
    _safe_id(emergency_id)
    bundle = await database.get_emergency_bundle(emergency_id)
    if not bundle:
        raise HTTPException(status_code=404, detail="Emergency not found")
    return bundle


def _safe_id(emergency_id: str) -> str:
    import re

    if not re.match(
        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", emergency_id or ""
    ):
        raise HTTPException(status_code=400, detail="Malformed emergency_id")
    return emergency_id


# ──────────────────────────────────────────────────────────────────────
# Listing / detail
# ──────────────────────────────────────────────────────────────────────

@router.get("/snapshot")
async def snapshot(limit: int = Query(default=100, ge=1, le=500)):
    """Everything the dashboard needs for its first paint, in one request."""
    rows = await database.list_emergencies(limit=limit)
    emergencies = [
        serialize_summary(
            row,
            live_audio=manager.audio_streaming(row["emergency_id"]),
            listener_count=manager.listener_count(row["emergency_id"]),
        )
        for row in rows
    ]
    return {
        "emergencies": emergencies,
        "stats": await database.dashboard_stats(),
        "realtime": manager.snapshot(),
        "server_config": {
            "countdown_seconds": settings.countdown_seconds,
            "location_interval_seconds": settings.location_interval_seconds,
            "audio_segment_seconds": settings.audio_segment_seconds,
            "stale_after_seconds": settings.stale_after_seconds,
            "digipin": digipin_status(),
        },
    }


@router.get("/emergencies")
async def list_emergencies(
    status: Optional[str] = Query(default=None, pattern=r"^(emergency|verifying|cancelled|resolved)$"),
    active_only: bool = Query(default=False),
    user_id: Optional[str] = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=500),
):
    rows = await database.list_emergencies(
        status=status, user_id=user_id, limit=limit, active_only=active_only
    )
    emergencies = [
        serialize_summary(
            row,
            live_audio=manager.audio_streaming(row["emergency_id"]),
            listener_count=manager.listener_count(row["emergency_id"]),
        )
        for row in rows
    ]
    return {"emergencies": emergencies, "count": len(emergencies)}


@router.get("/emergencies/{emergency_id}")
async def emergency_detail(emergency_id: str):
    bundle = await _detail(emergency_id)
    return serialize_detail(
        bundle,
        live_audio=manager.audio_streaming(emergency_id),
        listener_count=manager.listener_count(emergency_id),
    )


@router.get("/emergencies/{emergency_id}/events")
async def emergency_events(emergency_id: str, limit: int = Query(default=500, ge=1, le=2000)):
    await _detail(emergency_id)
    from serializers import serialize_event

    events = await database.get_events(emergency_id, limit=limit)
    return {"emergency_id": emergency_id, "count": len(events),
            "events": [serialize_event(e) for e in events]}


@router.get("/emergencies/{emergency_id}/locations")
async def emergency_locations(emergency_id: str, limit: int = Query(default=2000, ge=1, le=5000)):
    await _detail(emergency_id)
    from serializers import serialize_location

    locations = await database.get_locations(emergency_id, limit=limit)
    return {
        "emergency_id": emergency_id,
        "count": len(locations),
        "locations": [serialize_location(l) for l in locations],
    }


# ──────────────────────────────────────────────────────────────────────
# Authority actions
# ──────────────────────────────────────────────────────────────────────

@router.post("/emergencies/{emergency_id}/resolve")
async def resolve_emergency(
    emergency_id: str,
    data: ResolveRequest = ResolveRequest(),
    session: dict = Depends(get_authority),
):
    """Authority closes the emergency (e.g. responders arrived, user safe)."""
    record = await _detail(emergency_id)
    if record["status"] not in ("emergency", "verifying"):
        return {"status": "ok", "new_status": record["status"], "changed": False}

    operator = session.get("sub") or "authority"
    changed = await database.end_emergency(
        emergency_id, "resolved", ended_by=f"dashboard:{operator}",
        note=data.note or "Resolved from the authority dashboard",
    )
    if changed:
        logger.warning("Emergency %s resolved by dashboard operator %s", emergency_id, operator)
        await notify.push_emergency_ended(emergency_id, "resolved", f"dashboard:{operator}")
    return {"status": "ok", "emergency_id": emergency_id, "new_status": "resolved", "changed": changed}


@router.post("/emergencies/{emergency_id}/request-live-audio")
async def request_live_audio(
    emergency_id: str,
    enabled: bool = Query(default=True),
    session: dict = Depends(get_authority),
):
    """Ask the phone to start/stop the live audio stream (best effort)."""
    await _detail(emergency_id)
    delivered = await manager.send_to_device(
        emergency_id,
        {"type": "live_audio.request", "enabled": enabled, "from": session.get("sub", "authority")},
    )
    return {
        "status": "ok",
        "delivered_to_device": delivered,
        "note": "The phone only streams while its foreground service is running",
    }


# ──────────────────────────────────────────────────────────────────────
# Audio
# ──────────────────────────────────────────────────────────────────────

@router.get("/emergencies/{emergency_id}/audio/segments")
async def list_segments(emergency_id: str):
    await _detail(emergency_id)
    segments = await database.list_audio_segments(emergency_id)
    return {
        "emergency_id": emergency_id,
        "count": len(segments),
        "total_bytes": sum(s.get("size_bytes") or 0 for s in segments),
        "segments": [serialize_segment(s, emergency_id) for s in segments],
    }


@router.get("/emergencies/{emergency_id}/audio/segments/{segment_id}")
async def stream_segment(
    emergency_id: str,
    segment_id: str,
    range_header: Optional[str] = Header(default=None, alias="Range"),
    download: bool = Query(default=False),
):
    await _detail(emergency_id)
    segment = await database.get_audio_segment_record(emergency_id, segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="Audio segment not found for this emergency")
    return stream_file(
        segment["file_path"],
        segment.get("mime_type") or "audio/wav",
        range_header,
        filename=segment.get("file_name"),
        download=download,
    )


@router.get("/emergencies/{emergency_id}/live-audio/state")
async def live_audio_state(emergency_id: str):
    await _detail(emergency_id)
    return {
        "emergency_id": emergency_id,
        "device_streaming": manager.audio_streaming(emergency_id),
        "listeners": manager.listener_count(emergency_id),
        "websocket_endpoint": f"/ws/live-audio/{emergency_id}",
    }


# ──────────────────────────────────────────────────────────────────────
# Devices / health
# ──────────────────────────────────────────────────────────────────────

@router.get("/devices")
async def devices(user_id: Optional[str] = Query(default=None, max_length=64)):
    rows = await database.list_devices(user_id)
    return {"count": len(rows), "devices": [serialize_device(d) for d in rows]}


@router.get("/stats")
async def stats():
    return {
        "stats": await database.dashboard_stats(),
        "realtime": manager.snapshot(),
    }


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "env": settings.env,
        "realtime": manager.snapshot(),
        "digipin": digipin_status(),
    }
