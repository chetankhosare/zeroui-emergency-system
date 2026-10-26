"""
Response serializers
====================

One place decides how an emergency looks over the API, including derived
fields the dashboard depends on:

* ``connection_state`` — ``live`` / ``stale`` / ``ended`` (staleness is derived
  from ``last_update_at`` and ``ZEROUI_STALE_AFTER_SECONDS``),
* ``duration_seconds`` — activation → end (or now while active),
* ``live_audio`` — whether the device is currently streaming and how many
  dashboard listeners are attached for *this* emergency only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import settings

ACTIVE_STATUSES = {"emergency", "verifying"}


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _seconds_between(start: Optional[str], end: Optional[str]) -> Optional[float]:
    a, b = _parse(start), _parse(end)
    if not a or not b:
        return None
    return round((b - a).total_seconds(), 1)


def connection_state(record: Dict[str, Any], now: Optional[datetime] = None) -> str:
    status = record.get("status")
    if status not in ACTIVE_STATUSES:
        return "ended"
    now = now or datetime.now(timezone.utc)
    last = _parse(record.get("last_update_at") or record.get("created_at"))
    if not last:
        return "stale"
    age = (now - last).total_seconds()
    return "live" if age <= settings.stale_after_seconds else "stale"


def age_seconds(record: Dict[str, Any], now: Optional[datetime] = None) -> Optional[float]:
    now = now or datetime.now(timezone.utc)
    last = _parse(record.get("last_update_at") or record.get("created_at"))
    if not last:
        return None
    return round((now - last).total_seconds(), 1)


def serialize_device(device: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not device:
        return None
    return {
        "device_id": device.get("device_id"),
        "user_id": device.get("user_id"),
        "display_name": device.get("display_name"),
        "platform": device.get("platform"),
        "manufacturer": device.get("manufacturer"),
        "model": device.get("model"),
        "os_version": device.get("os_version"),
        "app_version": device.get("app_version"),
        "capabilities": device.get("capabilities") or {},
        "created_at": device.get("created_at"),
        "last_seen_at": device.get("last_seen_at"),
    }


def serialize_location(location: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not location:
        return None
    return {
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "digipin": location.get("digipin"),
        "digipin_available": bool(location.get("digipin_available", True)),
        "accuracy_m": location.get("accuracy_m"),
        "altitude_m": location.get("altitude_m"),
        "speed_mps": location.get("speed_mps"),
        "heading_deg": location.get("heading_deg"),
        "provider": location.get("provider"),
        "is_mock": bool(location.get("is_mock", False)),
        "timestamp": location.get("timestamp"),
    }


def serialize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": event.get("id"),
        "event_type": event.get("event_type"),
        "description": event.get("description"),
        "payload": event.get("payload") or {},
        "source": event.get("source"),
        "timestamp": event.get("timestamp"),
    }


def serialize_segment(segment: Dict[str, Any], emergency_id: str) -> Dict[str, Any]:
    """Segment metadata; the binary is fetched from a per-emergency URL."""
    return {
        "segment_id": segment.get("segment_id"),
        "emergency_id": emergency_id,
        "sequence": segment.get("sequence"),
        "file_name": segment.get("file_name"),
        "mime_type": segment.get("mime_type"),
        "size_bytes": segment.get("size_bytes"),
        "duration_seconds": segment.get("duration_seconds"),
        "sample_rate": segment.get("sample_rate"),
        "channels": segment.get("channels"),
        "codec": segment.get("codec"),
        "started_at": segment.get("started_at"),
        "ended_at": segment.get("ended_at"),
        "created_at": segment.get("created_at"),
        "url": f"/api/dashboard/emergencies/{emergency_id}/audio/segments/{segment.get('segment_id')}",
    }


def serialize_summary(
    record: Dict[str, Any],
    live_audio: bool = False,
    listener_count: int = 0,
) -> Dict[str, Any]:
    emergency_id = record.get("emergency_id")
    return {
        "emergency_id": emergency_id,
        "id": emergency_id,  # alias kept for convenience in the UI
        "user_id": record.get("user_id"),
        "device_id": record.get("device_id"),
        "device": serialize_device(record.get("device")),
        "status": record.get("status"),
        "connection_state": connection_state(record),
        "age_seconds": age_seconds(record),
        "trigger_type": record.get("trigger_type"),
        "trigger_metadata": record.get("trigger_metadata") or {},
        "latitude": record.get("latitude"),
        "longitude": record.get("longitude"),
        "digipin": record.get("digipin"),
        "digipin_available": bool(record.get("digipin_available", True)),
        "location_accuracy_m": record.get("location_accuracy_m"),
        "location_provider": record.get("location_provider"),
        "telemetry": record.get("telemetry") or {},
        "created_at": record.get("created_at"),
        "activated_at": record.get("activated_at"),
        "last_update_at": record.get("last_update_at"),
        "ended_at": record.get("ended_at"),
        "ended_by": record.get("ended_by"),
        "resolution_note": record.get("resolution_note"),
        "cancel_reason": record.get("cancel_reason"),
        "duration_seconds": _seconds_between(
            record.get("activated_at") or record.get("created_at"),
            record.get("ended_at") or datetime.now(timezone.utc).isoformat(),
        ),
        "event_count": record.get("event_count", 0),
        "last_event_at": record.get("last_event_at"),
        "audio_segment_count": record.get("audio_segment_count", 0),
        "audio_total_bytes": record.get("audio_total_bytes", 0),
        "audio_total_seconds": record.get("audio_total_seconds", 0),
        "has_audio": bool(record.get("has_audio")),
        "latest_location": serialize_location(record.get("latest_location")),
        "live_audio": {
            "device_streaming": live_audio,
            "listeners": listener_count,
        },
    }


def serialize_detail(
    bundle: Dict[str, Any],
    live_audio: bool = False,
    listener_count: int = 0,
) -> Dict[str, Any]:
    emergency_id = bundle["emergency_id"]
    data = serialize_summary(bundle, live_audio=live_audio, listener_count=listener_count)
    data["events"] = [serialize_event(e) for e in bundle.get("events", [])]
    data["locations"] = [serialize_location(l) for l in bundle.get("locations", [])]
    data["audio_segments"] = [
        serialize_segment(s, emergency_id) for s in bundle.get("audio_segments", [])
    ]
    if not data.get("latest_location") and data["locations"]:
        data["latest_location"] = data["locations"][-1]
    return data


def serialize_device_ack(record: Dict[str, Any]) -> Dict[str, Any]:
    """Compact payload returned to the phone (no dashboard-only fields)."""
    return {
        "emergency_id": record.get("emergency_id"),
        "status": record.get("status"),
        "digipin": record.get("digipin"),
        "digipin_available": bool(record.get("digipin_available", True)),
        "latitude": record.get("latitude"),
        "longitude": record.get("longitude"),
        "created_at": record.get("created_at"),
        "last_update_at": record.get("last_update_at"),
    }
