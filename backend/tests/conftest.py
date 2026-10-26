"""
Shared pytest fixtures
======================

Environment is configured *before* the backend modules are imported so the
tests always run against a throwaway database and audio directory, never
against ``backend/data``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

TMP_DIR = tempfile.mkdtemp(prefix="zeroui-tests-")

os.environ["ZEROUI_ENV"] = "development"
os.environ["ZEROUI_DB_PATH"] = os.path.join(TMP_DIR, "test.db")
os.environ["ZEROUI_AUDIO_DIR"] = os.path.join(TMP_DIR, "audio")
os.environ["ZEROUI_DASHBOARD_TOKEN"] = "test-dashboard-token"
os.environ["ZEROUI_ENROLLMENT_TOKEN"] = "test-enroll-token"
os.environ["ZEROUI_JWT_SECRET"] = "test-jwt-secret-0123456789abcdefghij"
os.environ["ZEROUI_LOG_LEVEL"] = "WARNING"
# Make sure SMS can never reach the network during tests
os.environ["TWILIO_ACCOUNT_SID"] = ""
os.environ["TWILIO_AUTH_TOKEN"] = ""
os.environ["TWILIO_PHONE_NUMBER"] = ""

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from fastapi.testclient import TestClient  # noqa: E402

import main as app_main  # noqa: E402

DASHBOARD_TOKEN = "test-dashboard-token"
ENROLLMENT_TOKEN = "test-enroll-token"


@pytest.fixture(scope="session")
def client():
    with TestClient(app_main.app) as test_client:
        yield test_client


@pytest.fixture
def enroll(client):
    """Enrol a fresh device for a fresh user (a "phone")."""
    created: list[str] = []

    def _enroll(user_id: str | None = None, display_name: str = "Test Phone", **extra):
        user_id = user_id or f"user_{uuid.uuid4().hex[:10]}"
        response = client.post(
            "/api/devices/register",
            headers={"X-Enrollment-Token": ENROLLMENT_TOKEN},
            json={
                "user_id": user_id,
                "display_name": display_name,
                "platform": "android",
                "manufacturer": "TestVendor",
                "model": "TestModel",
                "os_version": "14",
                "app_version": "2.0.0",
                "capabilities": {"accelerometer": True, "gps": True, "microphone": True},
                **extra,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        created.append(user_id)
        return {
            "user_id": user_id,
            "device_id": body["device_id"],
            "token": body["device_token"],
            "headers": {"X-Device-Token": body["device_token"]},
        }

    return _enroll


@pytest.fixture
def authority(client):
    """Dashboard session headers (authority role)."""
    response = client.post(
        "/api/auth/dashboard/login",
        json={"token": DASHBOARD_TOKEN, "operator": "pytest"},
    )
    assert response.status_code == 200, response.text
    token = response.json()["session_token"]
    return {"Authorization": f"Bearer {token}"}


def start_emergency(client, device, *, lat=19.8762, lng=75.3433, trigger="fall", **extra):
    """Helper: activate an emergency exactly like the phone does."""
    payload = {
        "trigger_type": trigger,
        "latitude": lat,
        "longitude": lng,
        "accuracy_m": 8.5,
        "location_provider": "gps",
        "countdown_seconds": 15,
        "capabilities": {"accelerometer": True, "gps": True, "microphone": True},
        "trigger_metadata": {
            "impact_magnitude": 31.4,
            "freefall_ms": 320,
            "inactivity_ms": 2500,
        },
        "client_emergency_id": f"local-{uuid.uuid4().hex[:12]}",
        "event_logs": [
            {
                "event_type": "fall_detected",
                "description": "Impact 31.4 m/s² then 2.5 s of inactivity",
            },
            {
                "event_type": "countdown_started",
                "description": "15 s cancel window started",
            },
        ],
        **extra,
    }
    return client.post("/api/emergencies/start", json=payload, headers=device["headers"])


@pytest.fixture
def start(client):
    def _start(device, **kwargs):
        response = start_emergency(client, device, **kwargs)
        assert response.status_code == 200, response.text
        return response.json()["emergency"]

    return _start
