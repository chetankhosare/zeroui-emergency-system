"""Dashboard authentication — exchange the shared token for a JWT session."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from config import settings
from models import DashboardLogin
from security import constant_time_equals, create_dashboard_session, verify_authority_ws  # noqa: F401

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/dashboard/login")
async def dashboard_login(data: DashboardLogin):
    """Validate the shared dashboard token and return a session token."""
    supplied = (data.token or "").strip()
    if not supplied or not constant_time_equals(supplied, settings.dashboard_token):
        logger.warning("Rejected dashboard login attempt (operator=%s)", data.operator)
        raise HTTPException(status_code=401, detail="Invalid dashboard token")

    session = create_dashboard_session(operator=data.operator or "authority")
    logger.info("Dashboard session issued for operator '%s'", session["operator"])
    return session


@router.get("/dashboard/session")
async def dashboard_session_info():
    """Describe how the dashboard should authenticate (never leaks the token)."""
    return {
        "scheme": "bearer-jwt",
        "login_endpoint": "/api/auth/dashboard/login",
        "websocket_token_param": "token",
        "session_minutes": settings.dashboard_session_minutes,
    }
