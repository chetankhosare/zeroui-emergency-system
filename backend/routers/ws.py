"""
Realtime WebSocket endpoints
============================

``/ws/dashboard``            authority control channel (JSON, server → client)
``/ws/device/{emergency_id}`` phone ingest: JSON control + binary live PCM
``/ws/live-audio/{emergency_id}`` dashboard listen-only live audio for ONE emergency

Live audio is relayed strictly per ``emergency_id``: a listener socket is only
ever attached to the emergency id in its own URL, and frames are copied from
that emergency's device socket to that emergency's listener set.  There is no
code path that fans audio across emergencies.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

import database
import notify
from config import settings
from digipin_service import generate_digipin_with_reason
from models import EmergencyEventRequest, LocationUpdate, TelemetryUpdate
from realtime import manager
from security import verify_authority_ws, verify_device_ws
from serializers import serialize_detail, serialize_event

logger = logging.getLogger(__name__)

router = APIRouter(tags=["realtime"])

# Last time a heavy broadcast was sent per emergency (throttling)
_last_push: Dict[str, float] = {}
# Advertised live-audio format per emergency (for the listener handshake)
_audio_format: Dict[str, dict] = {}
# Last time we logged a dropped live-audio frame
_last_drop_log: Dict[str, float] = {}

DEFAULT_AUDIO_FORMAT = {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _throttled(emergency_id: str, key: str, seconds: float = 4.0) -> bool:
    """True when a broadcast for this key may be sent now."""
    token = f"{emergency_id}:{key}"
    now = time.monotonic()
    last = _last_push.get(token, 0.0)
    if now - last < seconds:
        return False
    _last_push[token] = now
    return True


async def _dashboard_snapshot() -> dict:
    rows = await database.list_emergencies(limit=100)
    from serializers import serialize_summary

    return {
        "type": "hello",
        "emergencies": [
            serialize_summary(
                row,
                live_audio=manager.audio_streaming(row["emergency_id"]),
                listener_count=manager.listener_count(row["emergency_id"]),
            )
            for row in rows
        ],
        "stats": await database.dashboard_stats(),
        "realtime": manager.snapshot(),
        "server_config": {
            "countdown_seconds": settings.countdown_seconds,
            "location_interval_seconds": settings.location_interval_seconds,
            "audio_segment_seconds": settings.audio_segment_seconds,
            "stale_after_seconds": settings.stale_after_seconds,
        },
        "ts": _now_iso(),
    }


# ──────────────────────────────────────────────────────────────────────
# Dashboard control channel
# ──────────────────────────────────────────────────────────────────────

@router.websocket("/ws/dashboard")
async def dashboard_socket(websocket: WebSocket):
    session = await verify_authority_ws(websocket)
    if not session:
        await websocket.close(code=4401, reason="Dashboard authentication required")
        return

    await websocket.accept()
    await manager.connect_dashboard(websocket)
    try:
        await websocket.send_json(await _dashboard_snapshot())
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if message.get("type") == "ping":
                await websocket.send_json({"type": "pong", "ts": _now_iso()})
            elif message.get("type") == "refresh":
                await websocket.send_json(await _dashboard_snapshot())
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover - transport level
        logger.warning("Dashboard socket error: %s", exc)
    finally:
        await manager.disconnect_dashboard(websocket)


# ──────────────────────────────────────────────────────────────────────
# Device ingest channel
# ──────────────────────────────────────────────────────────────────────

@router.websocket("/ws/device/{emergency_id}")
async def device_socket(websocket: WebSocket, emergency_id: str):
    session = await verify_device_ws(websocket)
    if not session:
        await websocket.close(code=4401, reason="Device authentication required")
        return

    record = await database.get_emergency(emergency_id)
    if not record or record["user_id"] != session["user_id"]:
        await websocket.close(code=4404, reason="Emergency not found for this device")
        return

    await websocket.accept()
    await manager.connect_device(emergency_id, websocket)
    logger.info("Device channel open for emergency %s (user=%s)", emergency_id, session["user_id"])

    try:
        await websocket.send_json(
            {
                "type": "ready",
                "emergency_id": emergency_id,
                "status": record["status"],
                "server_time": _now_iso(),
                "live_audio": {"endpoint": f"/ws/live-audio/{emergency_id}", "format": DEFAULT_AUDIO_FORMAT},
                "location_interval_seconds": settings.location_interval_seconds,
                "audio_segment_seconds": settings.audio_segment_seconds,
                "stale_after_seconds": settings.stale_after_seconds,
            }
        )

        while True:
            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                break

            text = message.get("text")
            data = message.get("bytes")

            # ── Binary live-audio frame ──
            if data is not None:
                if len(data) > settings.max_live_frame_bytes:
                    now = time.monotonic()
                    if now - _last_drop_log.get(emergency_id, 0) > 10:
                        _last_drop_log[emergency_id] = now
                        logger.warning(
                            "Dropped oversized live-audio frame (%d bytes) for %s",
                            len(data), emergency_id,
                        )
                    continue
                await manager.relay_audio(emergency_id, data)
                continue

            if text is None:
                continue

            try:
                payload = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                await websocket.send_json({"type": "error", "detail": "invalid JSON"})
                continue

            try:
                await _handle_device_message(emergency_id, session, websocket, payload)
            except ValidationError as exc:
                await websocket.send_json(
                    {"type": "error", "kind": payload.get("type"), "detail": exc.errors()[:3]}
                )
            except Exception as exc:  # keep the socket alive
                logger.error("Device channel error (%s): %s", emergency_id, exc, exc_info=True)
                await websocket.send_json({"type": "error", "detail": str(exc)})

    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover
        logger.warning("Device socket error for %s: %s", emergency_id, exc)
    finally:
        await manager.disconnect_device(emergency_id, websocket)
        await manager.set_audio_producing(emergency_id, websocket, False)
        _audio_format.pop(emergency_id, None)
        await notify.push_live_audio_state(emergency_id)
        logger.info("Device channel closed for emergency %s", emergency_id)


async def _handle_device_message(
    emergency_id: str, device: dict, websocket: WebSocket, payload: dict
) -> None:
    kind = payload.get("type")

    if kind == "ping":
        await websocket.send_json({"type": "pong", "ts": _now_iso()})
        return

    if kind == "heartbeat":
        await database.touch_emergency(emergency_id)
        await websocket.send_json({"type": "ack", "kind": "heartbeat", "ts": _now_iso()})
        if _throttled(emergency_id, "heartbeat", 15.0):
            await notify.push_emergency_updated(emergency_id, "heartbeat")
        return

    if kind == "location":
        data = LocationUpdate(**payload.get("data", payload))
        record = await database.get_emergency(emergency_id)
        if not record or record["status"] not in ("emergency", "verifying"):
            await websocket.send_json({"type": "error", "detail": "emergency already ended"})
            return
        digipin, reason = generate_digipin_with_reason(data.latitude, data.longitude)
        await database.update_emergency_location(emergency_id, data.model_dump(), digipin)
        event = await database.add_event(
            emergency_id,
            "location_update",
            f"Location ({data.latitude:.6f}, {data.longitude:.6f})"
            + (f" · DigiPIN {digipin}" if digipin else f" · DigiPIN unavailable ({reason})"),
            payload={"accuracy_m": data.accuracy_m, "provider": data.provider},
        )
        await websocket.send_json(
            {
                "type": "ack",
                "kind": "location",
                "digipin": digipin,
                "digipin_available": digipin is not None,
                "ts": _now_iso(),
            }
        )
        await notify.push_event_added(emergency_id, event)
        if _throttled(emergency_id, "location", 3.0):
            await notify.push_emergency_updated(emergency_id, "location")
        return

    if kind == "telemetry":
        data = TelemetryUpdate(**payload.get("data", payload))
        await database.set_telemetry(emergency_id, data.model_dump(exclude_none=True))
        await websocket.send_json({"type": "ack", "kind": "telemetry", "ts": _now_iso()})
        if _throttled(emergency_id, "telemetry", 4.0):
            await notify.push_emergency_updated(emergency_id, "telemetry")
        return

    if kind == "event":
        data = EmergencyEventRequest(**payload.get("data", payload))
        event = await database.add_event(
            emergency_id,
            data.event_type,
            data.description,
            payload=data.payload,
            source=data.source or "device",
            timestamp=data.timestamp,
        )
        await websocket.send_json({"type": "ack", "kind": "event", "ts": _now_iso()})
        await notify.push_event_added(emergency_id, event)
        return

    if kind == "audio_state":
        fmt = payload.get("format") or DEFAULT_AUDIO_FORMAT
        if payload.get("streaming"):
            _audio_format[emergency_id] = fmt
            await manager.set_audio_producing(emergency_id, websocket, True)
        else:
            await manager.set_audio_producing(emergency_id, websocket, False)
        await websocket.send_json(
            {"type": "ack", "kind": "audio_state", "listeners": manager.listener_count(emergency_id)}
        )
        await notify.push_live_audio_state(emergency_id)
        return

    if kind == "end":
        status = payload.get("status") or "resolved"
        if status not in ("cancelled", "resolved"):
            await websocket.send_json({"type": "error", "detail": "invalid end status"})
            return
        changed = await database.end_emergency(
            emergency_id, status, ended_by="device", note=payload.get("note")
        )
        await websocket.send_json({"type": "ack", "kind": "end", "status": status})
        if changed:
            await notify.push_emergency_ended(emergency_id, status, "device")
        return

    await websocket.send_json({"type": "error", "detail": f"unknown message type '{kind}'"})


# ──────────────────────────────────────────────────────────────────────
# Live audio listener channel (authority only, one emergency per socket)
# ──────────────────────────────────────────────────────────────────────

@router.websocket("/ws/live-audio/{emergency_id}")
async def live_audio_socket(websocket: WebSocket, emergency_id: str):
    session = await verify_authority_ws(websocket)
    if not session:
        await websocket.close(code=4401, reason="Dashboard authentication required")
        return

    record = await database.get_emergency(emergency_id)
    if not record:
        await websocket.close(code=4404, reason="Emergency not found")
        return

    await websocket.accept()
    await manager.connect_listener(emergency_id, websocket)
    try:
        await websocket.send_json(
            {
                "type": "live_audio.state",
                "emergency_id": emergency_id,
                "streaming": manager.audio_streaming(emergency_id),
                "listeners": manager.listener_count(emergency_id),
                "format": _audio_format.get(emergency_id, DEFAULT_AUDIO_FORMAT),
                "ts": _now_iso(),
            }
        )
        await notify.push_live_audio_state(emergency_id)

        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            text = message.get("text")
            if text:
                try:
                    payload = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    continue
                if payload.get("type") == "ping":
                    await websocket.send_json({"type": "pong", "ts": _now_iso()})
                elif payload.get("type") == "state":
                    await websocket.send_json(
                        {
                            "type": "live_audio.state",
                            "emergency_id": emergency_id,
                            "streaming": manager.audio_streaming(emergency_id),
                            "listeners": manager.listener_count(emergency_id),
                            "format": _audio_format.get(emergency_id, DEFAULT_AUDIO_FORMAT),
                            "ts": _now_iso(),
                        }
                    )
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # pragma: no cover
        logger.warning("Live-audio socket error for %s: %s", emergency_id, exc)
    finally:
        await manager.disconnect_listener(emergency_id, websocket)
        await notify.push_live_audio_state(emergency_id)
