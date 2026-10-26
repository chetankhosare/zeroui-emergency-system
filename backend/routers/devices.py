"""Device enrolment — issues the private token the phone uses for every call."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header

import database
from config import settings
from digipin_service import digipin_status
from models import DeviceRegisterRequest, DeviceRegisterResponse
from security import get_device, hash_token, new_token, require_enrollment_token
from serializers import serialize_device, serialize_device_ack

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/devices", tags=["devices"])


@router.post("/register", response_model=DeviceRegisterResponse)
async def register_device(
    data: DeviceRegisterRequest,
    x_enrollment_token: str = Header(default=""),
):
    """Enrol one device and return its private ``device_token``."""
    require_enrollment_token(x_enrollment_token)

    token = new_token("zk_")
    record = await database.register_device(data.model_dump(), hash_token(token))
    logger.info(
        "Device enrolled: %s (user=%s, %s %s)",
        record["device_id"],
        record["user_id"],
        data.manufacturer,
        data.model,
    )
    return DeviceRegisterResponse(device_token=token, **record)


@router.get("/config")
async def device_config():
    """Public server configuration the app needs before/after enrolment."""
    return {
        "countdown_seconds": settings.countdown_seconds,
        "location_interval_seconds": settings.location_interval_seconds,
        "audio_segment_seconds": settings.audio_segment_seconds,
        "stale_after_seconds": settings.stale_after_seconds,
        "max_audio_segment_bytes": settings.max_audio_segment_bytes,
        "digipin": digipin_status(),
        "api_version": "2.0.0",
    }


@router.get("/me")
async def me(device: dict = Depends(get_device)):
    """Device identity + whether this user currently has an active emergency.

    The mobile app calls this on launch to recover state after the process was
    killed in the background.
    """
    active = await database.find_active_emergency(device["user_id"])
    return {
        "device": serialize_device(device),
        "active_emergency": serialize_device_ack(active) if active else None,
        "has_active_emergency": active is not None,
    }
