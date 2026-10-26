"""
Device API — emergency lifecycle
===============================

Every endpoint here is authenticated with the phone's private device token and
is **scoped to that device's user**:

* ``_owned()`` returns 404 (not 403) for another user's emergency, so an
  attacker cannot even probe which emergency ids exist,
* an emergency that already ended is immutable (409),
* one active emergency per user is enforced by a partial unique index.

Flow implemented here: start → locations (≈30 s) → telemetry → events →
1-minute audio segments → cancelled/resolved.
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile

import database
import notify
from config import settings
from digipin_service import generate_digipin_with_reason
from models import (
    EmergencyEventRequest,
    EmergencyStartRequest,
    EmergencyStatus,
    EmergencyStatusUpdate,
    LocationUpdate,
    TelemetryUpdate,
    validate_iso,
)
from security import get_device
from serializers import serialize_detail, serialize_segment, serialize_summary
from streaming import stream_file

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/emergencies", tags=["emergencies"])

EMERGENCY_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ALLOWED_AUDIO_EXT = {"wav", "webm", "m4a", "ogg", "opus", "aac", "mp3", "mp4"}
MIME_BY_EXT = {
    "wav": "audio/wav",
    "webm": "audio/webm",
    "m4a": "audio/mp4",
    "mp4": "audio/mp4",
    "ogg": "audio/ogg",
    "opus": "audio/ogg",
    "aac": "audio/aac",
    "mp3": "audio/mpeg",
}


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────

def _safe_emergency_id(value: str) -> str:
    if not EMERGENCY_ID_RE.match(value or ""):
        raise HTTPException(status_code=400, detail="Malformed emergency_id")
    return value


async def _owned(emergency_id: str, device: dict) -> dict:
    """Load an emergency the device is allowed to touch, else 404."""
    emergency_id = _safe_emergency_id(emergency_id)
    record = await database.get_emergency(emergency_id)
    if not record or record["user_id"] != device["user_id"]:
        raise HTTPException(status_code=404, detail="Emergency not found")
    return record


def _ensure_open(record: dict) -> None:
    if record["status"] not in ("emergency", "verifying"):
        raise HTTPException(
            status_code=409,
            detail=f"Emergency is already {record['status']} and can no longer be updated",
        )


def _segment_dir(emergency_id: str) -> str:
    path = os.path.join(settings.audio_dir, _safe_emergency_id(emergency_id))
    os.makedirs(path, exist_ok=True)
    return path


async def _send_sms_alerts(emergency_id: str, user_id: str, trigger_type: str,
                           lat: float, lng: float, digipin: Optional[str]) -> tuple[int, int]:
    """Best-effort SMS to the user's emergency contacts (Twilio, optional).

    Returns ``(sent, total)`` so the phone can report the outcome.
    """
    try:
        from sms_service import send_emergency_alerts

        contacts = await database.get_contacts(user_id)
        if not contacts:
            await database.add_event(
                emergency_id,
                "sms_skipped",
                "No emergency contacts configured — no SMS sent",
                source="backend",
            )
            logger.warning("Emergency %s: no emergency contacts for '%s'", emergency_id, user_id)
            return 0, 0

        results = send_emergency_alerts(
            contacts=contacts,
            user_id=user_id,
            trigger_type=trigger_type,
            lat=lat,
            lng=lng,
            digipin=digipin or "unavailable (outside DIGIPIN coverage)",
            dashboard_url=settings.dashboard_url_for_alerts,
        )
        sent = sum(1 for r in results if r.get("success"))
        event = await database.add_event(
            emergency_id,
            "sms_notification",
            f"SMS alerts: {sent}/{len(results)} contacts notified",
            payload={"results": [
                {"contact": r.get("contact"), "success": bool(r.get("success")), "error": r.get("error")}
                for r in results
            ]},
            source="backend",
        )
        await notify.push_event_added(emergency_id, event)
        return sent, len(results)
    except Exception as exc:  # pragma: no cover - SMS must never break the flow
        logger.error("SMS alert failure for %s: %s", emergency_id, exc, exc_info=True)
        return 0, 0


# ──────────────────────────────────────────────────────────────────────
# Start / read
# ──────────────────────────────────────────────────────────────────────

@router.post("/start")
async def start_emergency(
    data: EmergencyStartRequest,
    x_client_retry: Optional[str] = Header(default=None),
    device: dict = Depends(get_device),
):
    """Activate an emergency after the on-device countdown expired.

    Idempotent: retries carrying the same ``client_emergency_id`` return the
    already-created emergency instead of creating a duplicate.
    """
    user_id = device["user_id"]

    if data.client_emergency_id:
        existing = await database.find_by_client_id(data.client_emergency_id)
        if existing:
            return {
                "created": False,
                "duplicate": True,
                "emergency": serialize_summary(existing, live_audio=False),
            }

    active = await database.find_active_emergency(user_id)
    if active:
        logger.warning(
            "Rejected duplicate emergency for user %s (active=%s)", user_id, active["emergency_id"]
        )
        raise HTTPException(
            status_code=409,
            detail={
                "message": "User already has an active emergency",
                "emergency_id": active["emergency_id"],
                "status": active["status"],
            },
        )

    digipin, digipin_reason = generate_digipin_with_reason(data.latitude, data.longitude)

    try:
        record = await database.create_emergency(
            {
                "user_id": user_id,
                "device_id": device["device_id"],
                "status": EmergencyStatus.EMERGENCY.value,
                "trigger_type": data.trigger_type.value,
                "trigger_metadata": {
                    **data.trigger_metadata,
                    "countdown_seconds": data.countdown_seconds,
                    "client_emergency_id": data.client_emergency_id,
                    "digipin_unavailable_reason": digipin_reason,
                    # Distinguish how the incident started even when two
                    # different sources share the same trigger_type enum value
                    # (e.g. fall detector vs SOS gesture → 'manual').
                    "trigger_source": data.trigger_metadata.get("trigger_source") or data.trigger_type.value,
                },
                "latitude": data.latitude,
                "longitude": data.longitude,
                "digipin": digipin,
                "accuracy_m": data.accuracy_m,
                "location_provider": data.location_provider,
                "location_timestamp": data.location_timestamp,
                "telemetry": {
                    "capabilities": data.capabilities,
                    "device": data.device or {
                        "manufacturer": device.get("manufacturer"),
                        "model": device.get("model"),
                        "os_version": device.get("os_version"),
                        "app_version": device.get("app_version"),
                    },
                },
                "client_emergency_id": data.client_emergency_id,
            }
        )
    except database.ActiveEmergencyExists as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": "User already has an active emergency", "emergency_id": exc.emergency_id},
        )

    emergency_id = record["emergency_id"]
    logger.warning(
        "EMERGENCY ACTIVATED %s user=%s trigger=%s digipin=%s",
        emergency_id, user_id, data.trigger_type.value, digipin or "-",
    )

    # Replay the phone's local detection trail so the authority sees the whole story
    for entry in data.event_logs:
        event = await database.add_event(
            emergency_id,
            entry.event_type,
            entry.description,
            payload=entry.payload,
            source="device",
            timestamp=entry.timestamp,
        )
        await notify.push_event_added(emergency_id, event)

    activation_event = await database.add_event(
        emergency_id,
        "emergency_activated",
        f"Emergency activated ({data.trigger_type.value} trigger) after "
        f"{data.countdown_seconds if data.countdown_seconds is not None else settings.countdown_seconds}s countdown",
        payload={"trigger_metadata": data.trigger_metadata},
        source="device",
    )
    await notify.push_event_added(emergency_id, activation_event)
    await notify.push_emergency_created(emergency_id)

    # Notify the user's emergency contacts (no-op without Twilio credentials)
    sms_sent, sms_total = await _send_sms_alerts(
        emergency_id, user_id, data.trigger_type.value,
        data.latitude, data.longitude, digipin,
    )

    bundle = await database.get_emergency_bundle(emergency_id)
    return {
        "created": True,
        "duplicate": False,
        "digipin_available": digipin is not None,
        "digipin_unavailable_reason": digipin_reason,
        "sms_sent": sms_sent,
        "sms_total": sms_total,
        "emergency": serialize_detail(bundle or record, live_audio=False),
    }


@router.get("/active")
async def active_emergency(device: dict = Depends(get_device)):
    """The user's current active emergency, if any (used to recover after a restart)."""
    active = await database.find_active_emergency(device["user_id"])
    if not active:
        return {"has_active_emergency": False, "emergency": None}
    bundle = await database.get_emergency_bundle(active["emergency_id"])
    return {"has_active_emergency": True, "emergency": serialize_detail(bundle or active)}


@router.get("/{emergency_id}")
async def get_own_emergency(emergency_id: str, device: dict = Depends(get_device)):
    record = await _owned(emergency_id, device)
    bundle = await database.get_emergency_bundle(record["emergency_id"])
    return serialize_detail(bundle or record)


# ──────────────────────────────────────────────────────────────────────
# Live updates
# ──────────────────────────────────────────────────────────────────────

@router.post("/{emergency_id}/locations")
async def add_location(
    emergency_id: str, data: LocationUpdate, device: dict = Depends(get_device)
):
    record = await _owned(emergency_id, device)
    _ensure_open(record)

    digipin, reason = generate_digipin_with_reason(data.latitude, data.longitude)
    result = await database.update_emergency_location(
        emergency_id,
        {
            **data.model_dump(),
            "timestamp": data.timestamp,
        },
        digipin,
    )

    event = await database.add_event(
        emergency_id,
        "location_update",
        f"Location ({data.latitude:.6f}, {data.longitude:.6f})"
        + (f" · DigiPIN {digipin}" if digipin else f" · DigiPIN unavailable ({reason})")
        + (f" · ±{data.accuracy_m:.0f}m" if data.accuracy_m else ""),
        payload={"accuracy_m": data.accuracy_m, "provider": data.provider},
    )
    await notify.push_event_added(emergency_id, event)
    await notify.push_emergency_updated(emergency_id, "location")

    return {
        "status": "ok",
        "emergency_id": emergency_id,
        "digipin": digipin,
        "digipin_available": digipin is not None,
        "digipin_unavailable_reason": reason,
        "timestamp": result["timestamp"],
    }


@router.post("/{emergency_id}/telemetry")
async def add_telemetry(
    emergency_id: str, data: TelemetryUpdate, device: dict = Depends(get_device)
):
    record = await _owned(emergency_id, device)
    _ensure_open(record)
    await database.set_telemetry(emergency_id, data.model_dump(exclude_none=True))
    await notify.push_emergency_updated(emergency_id, "telemetry")
    return {"status": "ok"}


@router.post("/{emergency_id}/events")
async def add_event(
    emergency_id: str, data: EmergencyEventRequest, device: dict = Depends(get_device)
):
    record = await _owned(emergency_id, device)
    event = await database.add_event(
        emergency_id,
        data.event_type,
        data.description,
        payload=data.payload,
        source=data.source or "device",
        timestamp=data.timestamp,
    )
    await notify.push_event_added(emergency_id, event)
    await notify.push_emergency_updated(emergency_id, "event")
    return {"status": "ok", "event": event}


@router.post("/{emergency_id}/status")
async def change_status(
    emergency_id: str, data: EmergencyStatusUpdate, device: dict = Depends(get_device)
):
    """Cancel (user is safe, PIN required) or resolve (end of emergency)."""
    record = await _owned(emergency_id, device)

    if data.status == EmergencyStatus.CANCELLED:
        if not data.pin_verified:
            raise HTTPException(
                status_code=403,
                detail="Safety PIN confirmation is required to cancel an emergency",
            )
        if await database.user_has_pin(device["user_id"]):
            if not data.pin:
                raise HTTPException(status_code=400, detail="PIN is required to cancel")
            if not await database.verify_user_pin(device["user_id"], data.pin):
                logger.warning("Emergency %s: wrong PIN on cancel attempt", emergency_id)
                raise HTTPException(status_code=403, detail="Incorrect safety PIN")

    if record["status"] not in ("emergency", "verifying"):
        return {"status": "ok", "new_status": record["status"], "changed": False}

    changed = await database.end_emergency(
        emergency_id,
        data.status.value,
        ended_by="device",
        reason=data.reason,
        note=data.note,
    )
    if changed:
        logger.warning("Emergency %s -> %s (device)", emergency_id, data.status.value)
        await notify.push_emergency_ended(emergency_id, data.status.value, "device")

    return {
        "status": "ok",
        "emergency_id": emergency_id,
        "new_status": data.status.value,
        "changed": changed,
    }


# ──────────────────────────────────────────────────────────────────────
# Audio segments (~60 s each)
# ──────────────────────────────────────────────────────────────────────

@router.post("/{emergency_id}/audio/segments")
async def upload_audio_segment(
    emergency_id: str,
    file: UploadFile = File(...),
    sequence: int = Form(..., ge=1, le=100000),
    duration_seconds: Optional[float] = Form(default=None, ge=0, le=1800),
    sample_rate: Optional[int] = Form(default=None, ge=8000, le=48000),
    channels: Optional[int] = Form(default=None, ge=1, le=2),
    codec: Optional[str] = Form(default=None, max_length=32),
    started_at: Optional[str] = Form(default=None),
    ended_at: Optional[str] = Form(default=None),
    device: dict = Depends(get_device),
):
    record = await _owned(emergency_id, device)
    _ensure_open(record)

    raw_name = (file.filename or "").strip()
    ext = raw_name.rsplit(".", 1)[-1].lower() if "." in raw_name else "wav"
    if ext not in ALLOWED_AUDIO_EXT:
        raise HTTPException(status_code=415, detail=f"Unsupported audio format '.{ext}'")

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Empty audio segment")
    if len(payload) > settings.max_audio_segment_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Audio segment exceeds {settings.max_audio_segment_bytes} bytes",
        )

    directory = _segment_dir(emergency_id)
    file_name = f"segment_{sequence:04d}.{ext}"
    file_path = os.path.join(directory, file_name)
    with open(file_path, "wb") as handle:
        handle.write(payload)

    mime_type = file.content_type or MIME_BY_EXT.get(ext, "application/octet-stream")
    segment = await database.add_audio_segment(
        {
            "emergency_id": emergency_id,
            "sequence": sequence,
            "file_name": file_name,
            "file_path": file_path,
            "mime_type": mime_type,
            "size_bytes": len(payload),
            "duration_seconds": duration_seconds,
            "sample_rate": sample_rate,
            "channels": channels,
            "codec": codec,
            "started_at": validate_iso(started_at),
            "ended_at": validate_iso(ended_at),
        }
    )
    logger.info(
        "Emergency %s: audio segment #%s stored (%s bytes, %ss)",
        emergency_id, sequence, len(payload), duration_seconds,
    )

    event = await database.add_event(
        emergency_id,
        "audio_segment",
        f"Audio segment #{sequence} uploaded ({len(payload) / 1024:.0f} KB"
        + (f", {duration_seconds:.1f}s" if duration_seconds else "") + ")",
        payload={"sequence": sequence, "size_bytes": len(payload)},
    )
    await notify.push_event_added(emergency_id, event)
    await notify.push_segment_added(emergency_id, segment)

    return {"status": "ok", "segment": serialize_segment(segment, emergency_id)}


@router.get("/{emergency_id}/audio/segments")
async def list_segments(emergency_id: str, device: dict = Depends(get_device)):
    record = await _owned(emergency_id, device)
    segments = await database.list_audio_segments(record["emergency_id"])
    return {
        "emergency_id": record["emergency_id"],
        "count": len(segments),
        "segments": [serialize_segment(s, record["emergency_id"]) for s in segments],
    }


@router.get("/{emergency_id}/audio/segments/{segment_id}")
async def get_segment(
    emergency_id: str,
    segment_id: str,
    range_header: Optional[str] = Header(default=None, alias="Range"),
    device: dict = Depends(get_device),
):
    record = await _owned(emergency_id, device)
    segment = await database.get_audio_segment_record(record["emergency_id"], segment_id)
    if not segment:
        raise HTTPException(status_code=404, detail="Audio segment not found")
    return stream_file(
        segment["file_path"],
        segment.get("mime_type") or "audio/wav",
        range_header,
        filename=segment.get("file_name"),
    )


@router.get("/{emergency_id}/live-audio/state")
async def live_audio_state(
    emergency_id: str,
    listener: bool = Query(default=False),
    device: dict = Depends(get_device),
):
    """Polling fallback for the live-audio indicator."""
    from realtime import manager

    record = await _owned(emergency_id, device)
    return {
        "emergency_id": record["emergency_id"],
        "device_streaming": manager.audio_streaming(record["emergency_id"]),
        "listeners": manager.listener_count(record["emergency_id"]),
        "websocket_endpoint": f"/ws/device/{record['emergency_id']}",
    }
