"""Pydantic request/response models with real validation."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

EVENT_TYPE_RE = re.compile(r"^[a-z0-9_]{1,48}$")


def validate_iso(value: Optional[str]) -> Optional[str]:
    """Public wrapper around the ISO-8601 normaliser (used by routers)."""
    return _validate_iso(value)


def _validate_iso(value: Optional[str]) -> Optional[str]:
    """Accept an ISO-8601 timestamp and normalise it to UTC ISO-8601."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"invalid ISO-8601 timestamp: {value}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


class TriggerType(str, Enum):
    FALL = "fall"
    MANUAL = "manual"
    SHAKE = "shake"
    TEST = "test"


class EmergencyStatus(str, Enum):
    EMERGENCY = "emergency"      # active, authority visible
    VERIFYING = "verifying"      # reserved for pre-alert experiments (unused by default)
    CANCELLED = "cancelled"      # user confirmed safe before activation / during
    RESOLVED = "resolved"        # ended after activation

    @property
    def is_active(self) -> bool:
        return self in (EmergencyStatus.EMERGENCY, EmergencyStatus.VERIFYING)


# ── Devices ───────────────────────────────────────────────────────────────

class DeviceRegisterRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64)
    display_name: str = Field("", max_length=80)
    platform: str = Field("android", max_length=24)
    manufacturer: str = Field("", max_length=48)
    model: str = Field("", max_length=64)
    os_version: str = Field("", max_length=32)
    app_version: str = Field("", max_length=32)
    capabilities: Dict[str, Any] = Field(default_factory=dict)


class DeviceRegisterResponse(BaseModel):
    device_id: str
    device_token: str
    user_id: str


# ── Emergency lifecycle ───────────────────────────────────────────────────

class EventLog(BaseModel):
    event_type: str
    description: str = Field(..., max_length=500)
    timestamp: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_type")
    @classmethod
    def _check_type(cls, value: str) -> str:
        if not EVENT_TYPE_RE.match(value):
            raise ValueError("event_type must match ^[a-z0-9_]{1,48}$")
        return value

    @field_validator("timestamp")
    @classmethod
    def _check_ts(cls, value: Optional[str]) -> Optional[str]:
        return _validate_iso(value)


class EmergencyStartRequest(BaseModel):
    """Payload the phone sends when the 15 s countdown expires."""

    trigger_type: TriggerType
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    accuracy_m: Optional[float] = Field(None, ge=0, le=100000)
    altitude_m: Optional[float] = None
    location_provider: str = Field("unknown", max_length=32)
    location_timestamp: Optional[str] = None
    # Detection evidence (impact magnitude, free-fall ms, inactivity ms, ...)
    trigger_metadata: Dict[str, Any] = Field(default_factory=dict)
    capabilities: Dict[str, Any] = Field(default_factory=dict)
    device: Dict[str, Any] = Field(default_factory=dict)
    countdown_seconds: Optional[int] = Field(None, ge=0, le=600)
    client_emergency_id: Optional[str] = Field(None, max_length=64)
    event_logs: List[EventLog] = Field(default_factory=list)

    @field_validator("location_timestamp")
    @classmethod
    def _check_ts(cls, value: Optional[str]) -> Optional[str]:
        return _validate_iso(value)


class EmergencyStatusUpdate(BaseModel):
    status: EmergencyStatus
    reason: Optional[str] = Field(None, max_length=200)
    pin_verified: bool = False
    pin: Optional[str] = Field(None, min_length=4, max_length=4, pattern=r"^\d{4}$")
    note: Optional[str] = Field(None, max_length=500)

    @field_validator("status")
    @classmethod
    def _only_terminal(cls, value: EmergencyStatus) -> EmergencyStatus:
        if value.is_active:
            raise ValueError("status updates may only end an emergency (cancelled/resolved)")
        return value


class LocationUpdate(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    accuracy_m: Optional[float] = Field(None, ge=0, le=100000)
    altitude_m: Optional[float] = None
    speed_mps: Optional[float] = Field(None, ge=0, le=1000)
    heading_deg: Optional[float] = Field(None, ge=0, le=360)
    provider: str = Field("unknown", max_length=32)
    is_mock: bool = False
    timestamp: Optional[str] = None

    @field_validator("timestamp")
    @classmethod
    def _check_ts(cls, value: Optional[str]) -> Optional[str]:
        return _validate_iso(value)


class TelemetryUpdate(BaseModel):
    battery_pct: Optional[float] = Field(None, ge=0, le=100)
    charging: Optional[bool] = None
    network_type: Optional[str] = Field(None, max_length=32)
    connectivity: Optional[str] = Field(None, max_length=32)
    app_state: Optional[str] = Field(None, max_length=32)
    foreground_service: Optional[bool] = None
    screen_on: Optional[bool] = None
    audio_session_active: Optional[bool] = None
    audio_segment_sequence: Optional[int] = Field(None, ge=0)
    motion_state: Optional[str] = Field(None, max_length=32)
    accelerometer_magnitude: Optional[float] = None
    gps_enabled: Optional[bool] = None
    mic_available: Optional[bool] = None
    sensors: Dict[str, Any] = Field(default_factory=dict)
    extra: Dict[str, Any] = Field(default_factory=dict)


class EmergencyEventRequest(BaseModel):
    event_type: str
    description: str = Field(..., max_length=500)
    payload: Dict[str, Any] = Field(default_factory=dict)
    timestamp: Optional[str] = None
    source: str = Field("device", max_length=16)

    @field_validator("event_type")
    @classmethod
    def _check_type(cls, value: str) -> str:
        if not EVENT_TYPE_RE.match(value):
            raise ValueError("event_type must match ^[a-z0-9_]{1,48}$")
        return value

    @field_validator("timestamp")
    @classmethod
    def _check_ts(cls, value: Optional[str]) -> Optional[str]:
        return _validate_iso(value)


class ResolveRequest(BaseModel):
    note: Optional[str] = Field(None, max_length=500)


# ── PIN / contacts (unchanged public shape) ───────────────────────────────

class PinSet(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64)
    pin: str = Field(..., min_length=4, max_length=4, pattern=r"^\d{4}$")
    current_pin: Optional[str] = Field(None, min_length=4, max_length=4, pattern=r"^\d{4}$")


class PinVerify(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64)
    pin: str = Field(..., min_length=4, max_length=4, pattern=r"^\d{4}$")


class ContactCreate(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=80)
    phone_number: str = Field(..., min_length=10, max_length=20)
    relationship: str = Field("", max_length=40)


class DashboardLogin(BaseModel):
    token: Optional[str] = Field(None, max_length=200)
    operator: str = Field("authority", max_length=64)
