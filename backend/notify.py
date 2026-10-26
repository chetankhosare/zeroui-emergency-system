"""
Realtime push helpers
=====================

All routers funnel changes through these helpers so that every dashboard
receives exactly the same, consistently shaped messages.

Message envelope::

    {"type": "emergency.updated", "reason": "location", "emergency": {...}, "ts": "..."}

Types
-----
``hello``               initial snapshot / clock sync
``emergency.created``   a new active emergency exists
``emergency.updated``   status, location, telemetry or counts changed
``emergency.ended``     cancelled or resolved
``event.added``         a timeline entry was appended
``audio.segment``       a new ~60 s audio segment finished uploading
``live_audio.state``    the device started/stopped streaming live audio
``stats``               aggregate counters
``device.updated``      device enrolment/heartbeat
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import database
from realtime import manager
from serializers import serialize_event, serialize_segment, serialize_summary

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _summary(emergency_id: str) -> Optional[Dict[str, Any]]:
    record = await database.get_emergency(emergency_id)
    if not record:
        return None
    # get_emergency() does not aggregate counts; reuse the list path for a
    # complete summary (single emergency, so this stays cheap).
    rows = await database.list_emergencies(user_id=record["user_id"], limit=50)
    match = next((r for r in rows if r["emergency_id"] == emergency_id), None)
    record = match or record
    summary = serialize_summary(
        record,
        live_audio=manager.audio_streaming(emergency_id),
        listener_count=manager.listener_count(emergency_id),
    )
    return summary


async def push_emergency_created(emergency_id: str) -> None:
    summary = await _summary(emergency_id)
    if not summary:
        return
    await manager.broadcast_dashboard(
        {"type": "emergency.created", "emergency": summary, "ts": _now()}
    )
    await push_stats()


async def push_emergency_updated(emergency_id: str, reason: str = "update") -> None:
    summary = await _summary(emergency_id)
    if not summary:
        return
    await manager.broadcast_dashboard(
        {
            "type": "emergency.updated",
            "reason": reason,
            "emergency": summary,
            "ts": _now(),
        }
    )


async def push_emergency_ended(emergency_id: str, status: str, ended_by: str) -> None:
    summary = await _summary(emergency_id)
    if summary:
        await manager.broadcast_dashboard(
            {
                "type": "emergency.ended",
                "status": status,
                "ended_by": ended_by,
                "emergency": summary,
                "ts": _now(),
            }
        )
    # Tell the phone as well (dashboard-initiated resolution).
    await manager.send_to_device(
        emergency_id,
        {"type": "emergency.ended", "status": status, "ended_by": ended_by, "ts": _now()},
    )
    await push_stats()


async def push_event_added(emergency_id: str, event: Dict[str, Any]) -> None:
    await manager.broadcast_dashboard(
        {
            "type": "event.added",
            "emergency_id": emergency_id,
            "event": serialize_event(event),
            "ts": _now(),
        }
    )


async def push_segment_added(emergency_id: str, segment: Dict[str, Any]) -> None:
    await manager.broadcast_dashboard(
        {
            "type": "audio.segment",
            "emergency_id": emergency_id,
            "segment": serialize_segment(segment, emergency_id),
            "ts": _now(),
        }
    )
    await push_emergency_updated(emergency_id, "audio")


async def push_live_audio_state(emergency_id: str) -> None:
    await manager.broadcast_dashboard(
        {
            "type": "live_audio.state",
            "emergency_id": emergency_id,
            "streaming": manager.audio_streaming(emergency_id),
            "listeners": manager.listener_count(emergency_id),
            "ts": _now(),
        }
    )
    await push_emergency_updated(emergency_id, "live_audio")


async def push_device_updated(device_id: str) -> None:
    device = await database.get_device(device_id)
    if not device:
        return
    await manager.broadcast_dashboard(
        {"type": "device.updated", "device": device, "ts": _now()}
    )


async def push_stats() -> None:
    stats = await database.dashboard_stats()
    await manager.broadcast_dashboard(
        {"type": "stats", "stats": stats, "realtime": manager.snapshot(), "ts": _now()}
    )
