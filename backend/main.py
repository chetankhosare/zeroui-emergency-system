"""
Zero UI Emergency Assistance System — FastAPI backend
====================================================

Assembly point: configuration, lifespan (schema + migration), routers, static
hosting of the dashboard and the browser-based mobile simulator.

Run:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from database import (
    add_contact,
    dashboard_stats,
    get_contacts,
    get_user,
    init_db,
    remove_contact,
    set_user_pin,
    user_has_pin,
    verify_user_pin,
)
from models import ContactCreate, DashboardLogin, PinSet, PinVerify
from realtime import manager
from routers.auth import router as auth_router
from routers.dashboard import router as dashboard_router
from routers.devices import router as devices_router
from routers.emergencies import router as emergencies_router
from routers.ws import router as ws_router
from security import get_device
from serializers import serialize_device

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s",
)
logger = logging.getLogger("zeroui")


@asynccontextmanager
async def lifespan(app: FastAPI):
    summary = await init_db()
    os.makedirs(settings.audio_dir, exist_ok=True)
    logger.warning("Zero UI backend starting — %s", settings.as_log_dict())
    if summary.get("migrated"):
        logger.warning("Database migrated from the legacy schema: %s", summary["migrated"])
    if not settings.is_production:
        logger.warning(
            "Development auth tokens are active. Set ZEROUI_DASHBOARD_TOKEN / "
            "ZEROUI_ENROLLMENT_TOKEN / ZEROUI_JWT_SECRET in backend/.env before deploying."
        )
    yield
    logger.warning("Zero UI backend shutting down")


app = FastAPI(
    title="Zero UI Emergency Assistance System",
    version="2.0.0",
    description=(
        "Realtime emergency backend: device ingest (REST + WebSocket), live audio "
        "relay, DigiPIN location codes and the authority dashboard API."
    ),
    lifespan=lifespan,
)

# CORS: credentials are only allowed with explicit origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials="*" not in settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──
app.include_router(auth_router)
app.include_router(devices_router)
app.include_router(emergencies_router)
app.include_router(dashboard_router)
app.include_router(ws_router)


# ─── Safety PIN (device-scoped) ───────────────────────────────────────
# The PIN is the second factor that lets a user dismiss an emergency.  It is
# stored salted+hashed, and all PIN calls are scoped to the enrolled device's
# own user so nobody else can probe or brute-force another user's PIN.

@app.post("/api/pin/set", tags=["pin"])
async def set_pin(data: PinSet, device: dict = Depends(get_device)):
    if data.user_id != device["user_id"]:
        raise HTTPException(status_code=403, detail="Device is not enrolled for this user")

    if await user_has_pin(data.user_id):
        if not data.current_pin:
            raise HTTPException(status_code=403, detail="Current PIN required to change an existing PIN")
        if not await verify_user_pin(data.user_id, data.current_pin):
            raise HTTPException(status_code=403, detail="Current PIN is incorrect")

    await set_user_pin(data.user_id, data.pin)
    logger.info("Safety PIN set for user %s", data.user_id)
    return {"status": "ok", "message": f"Safety PIN set for user {data.user_id}"}


@app.post("/api/pin/verify", tags=["pin"])
async def verify_pin(data: PinVerify, device: dict = Depends(get_device)):
    if data.user_id != device["user_id"]:
        raise HTTPException(status_code=403, detail="Device is not enrolled for this user")
    if not await verify_user_pin(data.user_id, data.pin):
        logger.warning("Failed PIN verification for user %s", data.user_id)
        raise HTTPException(status_code=403, detail="Invalid PIN")
    return {"status": "ok", "verified": True}


@app.get("/api/pin/status/{user_id}", tags=["pin"])
async def pin_status(user_id: str, device: dict = Depends(get_device)):
    if user_id != device["user_id"]:
        raise HTTPException(status_code=403, detail="Device is not enrolled for this user")
    return {"user_id": user_id, "has_pin": await user_has_pin(user_id)}


# ─── Emergency contacts (device-scoped) ───────────────────────────────

@app.post("/api/contacts", tags=["contacts"])
async def create_contact(data: ContactCreate, device: dict = Depends(get_device)):
    if data.user_id != device["user_id"]:
        raise HTTPException(status_code=403, detail="Device is not enrolled for this user")
    contact_id = await add_contact(data.user_id, data.name, data.phone_number, data.relationship)
    return {"status": "ok", "contact_id": contact_id,
            "message": f"Contact '{data.name}' added for user {data.user_id}"}


@app.get("/api/contacts/{user_id}", tags=["contacts"])
async def list_contacts(user_id: str, device: dict = Depends(get_device)):
    if user_id != device["user_id"]:
        raise HTTPException(status_code=403, detail="Device is not enrolled for this user")
    contacts = await get_contacts(user_id)
    return {"user_id": user_id, "contacts": contacts, "count": len(contacts)}


@app.delete("/api/contacts/{contact_id}", tags=["contacts"])
async def delete_contact(contact_id: int, user_id: str = Query(...), device: dict = Depends(get_device)):
    if user_id != device["user_id"]:
        raise HTTPException(status_code=403, detail="Device is not enrolled for this user")
    if not await remove_contact(contact_id, user_id):
        raise HTTPException(status_code=404, detail="Contact not found")
    return {"status": "ok", "message": "Contact removed"}


@app.post("/api/contacts/test-sms", tags=["contacts"])
async def test_sms(user_id: str = Query(...), device: dict = Depends(get_device)):
    if user_id != device["user_id"]:
        raise HTTPException(status_code=403, detail="Device is not enrolled for this user")
    contacts = await get_contacts(user_id)
    if not contacts:
        raise HTTPException(status_code=404, detail="No contacts configured")

    from sms_service import send_sms

    message = (
        "✅ TEST from Zero UI Emergency System\n\n"
        f"This is a test notification for user: {user_id}\n"
        "Your emergency contact is set up correctly!\n"
        "You will receive alerts if an emergency is detected."
    )
    results = []
    for contact in contacts:
        result = send_sms(contact["phone_number"], message)
        result["contact"] = contact["name"]
        results.append(result)
    sent = sum(1 for r in results if r.get("success"))
    return {"sent": sent, "total": len(results), "results": results}


# ─── Public info / health ─────────────────────────────────────────────

@app.get("/health", tags=["meta"])
async def health():
    return {
        "status": "ok",
        "version": app.version,
        "env": settings.env,
        "auth": "device-token + dashboard-jwt",
        "realtime": manager.snapshot(),
    }


@app.get("/api/config", tags=["meta"])
async def public_config():
    """Public configuration used by the mobile app before enrolment."""
    from digipin_service import digipin_status

    return {
        "countdown_seconds": settings.countdown_seconds,
        "location_interval_seconds": settings.location_interval_seconds,
        "audio_segment_seconds": settings.audio_segment_seconds,
        "stale_after_seconds": settings.stale_after_seconds,
        "digipin": digipin_status(),
        "api_version": app.version,
    }


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Log unexpected errors with context instead of returning a bare 500."""
    logger.error("Unhandled error on %s %s: %s", request.method, request.url.path, exc,
                 exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ── Static hosting: dashboard + browser mobile simulator ──
if os.path.isdir(settings.dashboard_dir):
    app.mount("/dashboard", StaticFiles(directory=settings.dashboard_dir, html=True), name="dashboard")
    logger.info("Dashboard mounted at /dashboard from %s", settings.dashboard_dir)

if os.path.isdir(settings.mobile_simulator_dir):
    app.mount(
        "/mobile",
        StaticFiles(directory=settings.mobile_simulator_dir, html=True),
        name="mobile-simulator",
    )
    logger.info("Mobile simulator mounted at /mobile from %s", settings.mobile_simulator_dir)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/dashboard/")
