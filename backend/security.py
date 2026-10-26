"""
Authentication / authorisation helpers
=====================================

Prototype-appropriate but real authentication:

* **Devices** enrol once with the shared *enrollment token* and receive a
  private, per-device token.  Only the SHA-256 hash is stored in the database.
  Every device API call is scoped to the device's own ``user_id``, so one
  device can never read or modify another user's emergency.
* **Dashboard sessions** exchange the shared dashboard token for a short lived
  JWT.  The JWT is required for both REST and WebSocket access, so an unrelated
  browser session cannot pull emergency data.

Tokens can be supplied as ``Authorization: Bearer <token>``, ``X-Dashboard-Token``
or (for WebSocket / ``<audio>`` tags, which cannot set headers) as a ``?token=``
query parameter.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Header, HTTPException, Query, Request, WebSocket, status

from config import settings

logger = logging.getLogger(__name__)

DEVICE_HEADER = "x-device-token"
DASHBOARD_HEADER = "x-dashboard-token"


# ──────────────────────────────────────────────────────────────────────
# Token helpers
# ──────────────────────────────────────────────────────────────────────

def new_token(prefix: str = "") -> str:
    """Return a new opaque URL-safe token."""
    return f"{prefix}{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    """Deterministic hash used for token lookups."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def hash_pin(pin: str) -> str:
    """Hash a 4-digit safety PIN (salted with the configured secret)."""
    return hashlib.sha256(f"{settings.jwt_secret}:{pin}".encode("utf-8")).hexdigest()


def verify_pin_hash(pin: str, pin_hash: str) -> bool:
    return constant_time_equals(hash_pin(pin), pin_hash)


# ──────────────────────────────────────────────────────────────────────
# Dashboard sessions (JWT)
# ──────────────────────────────────────────────────────────────────────

def create_dashboard_session(operator: str = "authority") -> dict:
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.dashboard_session_minutes
    )
    payload = {
        "sub": operator,
        "role": "authority",
        "iat": int(datetime.now(timezone.utc).timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": "zeroui-backend",
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")
    return {
        "session_token": token,
        "expires_at": expires_at.isoformat(),
        "role": "authority",
        "operator": operator,
    }


def decode_dashboard_session(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer="zeroui-backend",
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Dashboard session expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid dashboard session")


# ──────────────────────────────────────────────────────────────────────
# Extraction of credentials from various transports
# ──────────────────────────────────────────────────────────────────────

def _extract_token(
    authorization: Optional[str],
    header_token: Optional[str],
    query_token: Optional[str],
) -> Optional[str]:
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return authorization.strip()
    if header_token:
        return header_token.strip()
    if query_token:
        return query_token.strip()
    return None


# ──────────────────────────────────────────────────────────────────────
# FastAPI dependencies
# ──────────────────────────────────────────────────────────────────────

async def get_authority(
    request: Request,
    authorization: Optional[str] = Header(default=None),
    x_dashboard_token: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
) -> dict:
    """Require a valid dashboard session (authority role)."""
    raw = _extract_token(authorization, x_dashboard_token, token)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Dashboard authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Shared token (used once to create a session, and accepted afterwards so
    # that scripts/tests can talk to the API directly).
    if constant_time_equals(raw, settings.dashboard_token):
        return {"role": "authority", "sub": "shared-token", "operator": "authority"}

    return decode_dashboard_session(raw)


async def verify_authority_ws(websocket: WebSocket) -> Optional[dict]:
    """WebSocket variant of :func:`get_authority` (returns None when rejected)."""
    raw = (
        websocket.query_params.get("token")
        or websocket.headers.get(DASHBOARD_HEADER)
        or websocket.headers.get("authorization", "").removeprefix("Bearer ").strip()
    )
    if not raw:
        return None
    if constant_time_equals(raw, settings.dashboard_token):
        return {"role": "authority", "sub": "shared-token", "operator": "authority"}
    try:
        return decode_dashboard_session(raw)
    except HTTPException:
        return None


async def get_device(
    authorization: Optional[str] = Header(default=None),
    x_device_token: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None),
) -> dict:
    """Require a valid enrolled device token; returns the device record."""
    from database import get_device_by_token  # local import avoids a cycle

    raw = _extract_token(authorization, x_device_token, token)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Device authentication required (X-Device-Token)",
        )
    device = await get_device_by_token(raw)
    if not device:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unknown or revoked device token",
        )
    return device


async def verify_device_ws(websocket: WebSocket) -> Optional[dict]:
    from database import get_device_by_token

    raw = websocket.query_params.get("token")
    if not raw:
        return None
    return await get_device_by_token(raw)


def require_enrollment_token(supplied: str) -> None:
    if not constant_time_equals(supplied, settings.enrollment_token):
        raise HTTPException(status_code=403, detail="Invalid enrollment token")
