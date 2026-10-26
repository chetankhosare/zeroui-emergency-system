"""
End-to-end acceptance test against a real server
===============================================

Spawns ``uvicorn`` on a free port with a throwaway database and drives the
complete flow with real HTTP and WebSocket clients:

1. two devices enrol (two different users),
2. the dashboard opens its control socket and authenticates,
3. emergency A activates → appears on the dashboard in realtime,
4. device A streams live PCM → only A's listener receives it,
5. emergency B activates at the same time → B's frames never reach A,
6. live location + a 1-minute audio segment reach the dashboard,
7. the authority resolves A; the phone and dashboard are both told,
8. B stays active and is resolved independently.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx
import pytest
from websockets.sync.client import connect

BACKEND_DIR = Path(__file__).resolve().parents[1]

DASHBOARD_TOKEN = "e2e-dashboard-token"
ENROLLMENT_TOKEN = "e2e-enroll-token"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def server():
    tmp = tempfile.mkdtemp(prefix="zeroui-e2e-")
    port = _free_port()
    env = {
        **os.environ,
        "ZEROUI_ENV": "development",
        "ZEROUI_DB_PATH": os.path.join(tmp, "e2e.db"),
        "ZEROUI_AUDIO_DIR": os.path.join(tmp, "audio"),
        "ZEROUI_DASHBOARD_TOKEN": DASHBOARD_TOKEN,
        "ZEROUI_ENROLLMENT_TOKEN": ENROLLMENT_TOKEN,
        "ZEROUI_JWT_SECRET": "e2e-jwt-secret",
        "ZEROUI_LOG_LEVEL": "WARNING",
        "TWILIO_ACCOUNT_SID": "",
        "TWILIO_AUTH_TOKEN": "",
        "TWILIO_PHONE_NUMBER": "",
    }
    process = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "main:app",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        ],
        cwd=str(BACKEND_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    ws_base = f"ws://127.0.0.1:{port}"

    deadline = time.time() + 45
    ready = False
    while time.time() < deadline:
        if process.poll() is not None:
            output = process.stdout.read().decode(errors="replace") if process.stdout else ""
            raise RuntimeError(f"uvicorn exited early:\n{output}")
        try:
            if httpx.get(f"{base}/health", timeout=2).status_code == 200:
                ready = True
                break
        except Exception:
            time.sleep(0.3)
    if not ready:
        process.terminate()
        raise RuntimeError("uvicorn did not become healthy in time")

    yield {"base": base, "ws": ws_base}

    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover
        process.kill()


def _enroll(http: httpx.Client, user_id: str) -> dict:
    response = http.post(
        "/api/devices/register",
        headers={"X-Enrollment-Token": ENROLLMENT_TOKEN},
        json={
            "user_id": user_id,
            "display_name": f"{user_id}'s phone",
            "platform": "android",
            "manufacturer": "Google",
            "model": "Pixel 5",
            "os_version": "14",
            "app_version": "2.0.0",
            "capabilities": {"accelerometer": True, "gyroscope": True, "gps": True, "microphone": True},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return {
        "user_id": user_id,
        "token": body["device_token"],
        "device_id": body["device_id"],
        "headers": {"X-Device-Token": body["device_token"]},
    }


def _start_emergency(http: httpx.Client, device: dict, lat: float, lng: float, trigger="fall") -> dict:
    response = http.post(
        "/api/emergencies/start",
        headers=device["headers"],
        json={
            "trigger_type": trigger,
            "latitude": lat,
            "longitude": lng,
            "accuracy_m": 7.0,
            "location_provider": "gps",
            "countdown_seconds": 15,
            "client_emergency_id": f"local-{uuid.uuid4().hex[:10]}",
            "trigger_metadata": {"impact_magnitude": 33.2, "freefall_ms": 280, "inactivity_ms": 2600},
            "capabilities": {"accelerometer": True, "gps": True, "microphone": True},
            "event_logs": [
                {"event_type": "fall_detected", "description": "Impact 33.2 m/s² + 2.6 s inactivity"},
                {"event_type": "countdown_started", "description": "15 s cancel window"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["emergency"]


def _wait_for(socket_conn, message_type: str, limit: int = 40, timeout: float = 6.0) -> dict:
    """Drain messages until one of ``message_type`` arrives."""
    for _ in range(limit):
        raw = socket_conn.recv(timeout=timeout)
        if isinstance(raw, (bytes, bytearray)):
            continue
        message = json.loads(raw)
        if message.get("type") == message_type:
            return message
    raise AssertionError(f"no '{message_type}' message received")


def test_complete_emergency_flow_two_users(server):
    base, ws_base = server["base"], server["ws"]

    with httpx.Client(base_url=base, timeout=15) as http:
        # ── authentication ──
        login = http.post("/api/auth/dashboard/login", json={"token": DASHBOARD_TOKEN, "operator": "e2e"})
        assert login.status_code == 200
        session = login.json()["session_token"]
        authority = {"Authorization": f"Bearer {session}"}

        # unauthenticated dashboard access is refused
        assert http.get("/api/dashboard/emergencies").status_code == 401

        device_a = _enroll(http, f"e2e_alice_{uuid.uuid4().hex[:6]}")
        device_b = _enroll(http, f"e2e_bob_{uuid.uuid4().hex[:6]}")

        # ── dashboard realtime socket ──
        with connect(f"{ws_base}/ws/dashboard?token={session}") as dashboard:
            hello = json.loads(dashboard.recv(timeout=6))
            assert hello["type"] == "hello"
            assert hello["server_config"]["countdown_seconds"] == 15

            # ── 1. emergency A activates ──
            emergency_a = _start_emergency(http, device_a, 19.8762, 75.3433)
            eid_a = emergency_a["emergency_id"]
            created = _wait_for(dashboard, "emergency.created")
            assert created["emergency"]["emergency_id"] == eid_a
            assert created["emergency"]["status"] == "emergency"
            assert created["emergency"]["digipin"] == "4C3-FJ6-PLK3"
            assert created["emergency"]["device"]["model"] == "Pixel 5"

            # ── 2. device A opens its realtime channel ──
            with connect(f"{ws_base}/ws/device/{eid_a}?token={device_a['token']}") as phone_a:
                ready = json.loads(phone_a.recv(timeout=6))
                assert ready["type"] == "ready"
                assert ready["live_audio"]["endpoint"] == f"/ws/live-audio/{eid_a}"

                # ── 3. live audio: listener for A ──
                with connect(f"{ws_base}/ws/live-audio/{eid_a}?token={session}") as listen_a:
                    state = json.loads(listen_a.recv(timeout=6))
                    assert state["type"] == "live_audio.state"
                    assert state["emergency_id"] == eid_a
                    assert state["streaming"] is False

                    phone_a.send(json.dumps({
                        "type": "audio_state",
                        "streaming": True,
                        "format": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1},
                    }))
                    ack = _wait_for(phone_a, "ack")
                    assert ack["kind"] == "audio_state"

                    phone_a.send(b"PCM-A-FRAME-0001")
                    assert listen_a.recv(timeout=6) == b"PCM-A-FRAME-0001"

                    # ── 4. emergency B activates concurrently ──
                    emergency_b = _start_emergency(http, device_b, 28.6139, 77.2090, trigger="manual")
                    eid_b = emergency_b["emergency_id"]
                    assert eid_b != eid_a
                    assert emergency_b["digipin"] == "39J-438-TJC7"

                    with connect(f"{ws_base}/ws/device/{eid_b}?token={device_b['token']}") as phone_b:
                        assert json.loads(phone_b.recv(timeout=6))["type"] == "ready"

                        with connect(f"{ws_base}/ws/live-audio/{eid_b}?token={session}") as listen_b:
                            assert json.loads(listen_b.recv(timeout=6))["emergency_id"] == eid_b

                            phone_b.send(json.dumps({"type": "audio_state", "streaming": True}))
                            _wait_for(phone_b, "ack")

                            phone_a.send(b"PCM-A-FRAME-0002")
                            phone_b.send(b"PCM-B-FRAME-0001")

                            # each listener receives only its own emergency's audio
                            assert listen_a.recv(timeout=6) == b"PCM-A-FRAME-0002"
                            assert listen_b.recv(timeout=6) == b"PCM-B-FRAME-0001"

                            with pytest.raises(TimeoutError):
                                listen_a.recv(timeout=1.5)  # B's frames never reach A

                            # ── 5. live location over the socket (≈30 s cadence) ──
                            phone_a.send(json.dumps({
                                "type": "location",
                                "data": {
                                    "latitude": 19.8770, "longitude": 75.3440,
                                    "accuracy_m": 6.0, "provider": "gps",
                                },
                            }))
                            location_ack = _wait_for(phone_a, "ack")
                            assert location_ack["kind"] == "location"
                            # real DIGIPIN for the updated coordinate (same grid cell prefix)
                            assert location_ack["digipin_available"] is True
                            assert location_ack["digipin"].startswith("4C3-FJ6")

                            updated = _wait_for(dashboard, "emergency.updated")
                            assert updated["emergency"]["emergency_id"] == eid_a

                            # ── 6. telemetry + event + 60 s audio segment ──
                            phone_a.send(json.dumps({
                                "type": "telemetry",
                                "data": {"battery_pct": 64, "network_type": "lte",
                                         "app_state": "foreground_service", "foreground_service": True,
                                         "audio_session_active": True,
                                         "sensors": {"accelerometer": True, "gps": True}},
                            }))
                            _wait_for(phone_a, "ack")

                            segment_upload = http.post(
                                f"/api/emergencies/{eid_a}/audio/segments",
                                headers=device_a["headers"],
                                files={"file": ("segment_0001.wav",
                                                b"RIFF" + b"\x00" * 60, "audio/wav")},
                                data={"sequence": 1, "duration_seconds": 60,
                                      "sample_rate": 16000, "channels": 1, "codec": "pcm_s16le"},
                            )
                            assert segment_upload.status_code == 200, segment_upload.text
                            segment_id = segment_upload.json()["segment"]["segment_id"]
                            segment_msg = _wait_for(dashboard, "audio.segment")
                            assert segment_msg["emergency_id"] == eid_a
                            assert segment_msg["segment"]["sequence"] == 1

                            # dashboard-only audio access is enforced per emergency
                            assert http.get(
                                f"/api/dashboard/emergencies/{eid_a}/audio/segments/{segment_id}",
                                headers=authority,
                            ).status_code == 200
                            assert http.get(
                                f"/api/dashboard/emergencies/{eid_b}/audio/segments/{segment_id}",
                                headers=authority,
                            ).status_code == 404

                            detail = http.get(
                                f"/api/dashboard/emergencies/{eid_a}", headers=authority
                            ).json()
                            assert detail["telemetry"]["battery_pct"] == 64
                            assert detail["connection_state"] == "live"
                            assert len(detail["locations"]) >= 2
                            assert detail["has_audio"] is True

                            # ── 7. authority resolves A; phone + dashboard informed ──
                            resolve = http.post(
                                f"/api/dashboard/emergencies/{eid_a}/resolve",
                                headers=authority,
                                json={"note": "Ambulance dispatched"},
                            )
                            assert resolve.status_code == 200 and resolve.json()["changed"] is True

                            ended_on_phone = _wait_for(phone_a, "emergency.ended")
                            assert ended_on_phone["status"] == "resolved"
                            ended_on_dashboard = _wait_for(dashboard, "emergency.ended")
                            assert ended_on_dashboard["emergency"]["emergency_id"] == eid_a
                            assert ended_on_dashboard["emergency"]["connection_state"] == "ended"

                            # ── 8. B is untouched and still active ──
                            detail_b = http.get(
                                f"/api/dashboard/emergencies/{eid_b}", headers=authority
                            ).json()
                            assert detail_b["status"] == "emergency"
                            assert detail_b["latitude"] == pytest.approx(28.6139)

                            phone_b.send(json.dumps({
                                "type": "location",
                                "data": {"latitude": 28.6200, "longitude": 77.2150, "provider": "gps"},
                            }))
                            _wait_for(phone_b, "ack")

                            final_b = http.get(
                                f"/api/dashboard/emergencies/{eid_b}", headers=authority
                            ).json()
                            assert final_b["latitude"] == pytest.approx(28.6200)
                            assert final_b["status"] == "emergency"

                            # the phone can also end its own emergency
                            end_response = http.post(
                                f"/api/emergencies/{eid_b}/status",
                                headers=device_b["headers"],
                                json={"status": "resolved", "note": "user ended emergency"},
                            )
                            assert end_response.status_code == 200
                            assert http.get(
                                f"/api/dashboard/emergencies/{eid_b}", headers=authority
                            ).json()["status"] == "resolved"

        # ── final state: no active emergencies, all data preserved ──
        snapshot = http.get("/api/dashboard/snapshot", headers=authority).json()
        assert snapshot["stats"]["active"] == 0
        assert snapshot["stats"]["resolved"] >= 2
        assert snapshot["stats"]["devices"] >= 2
        assert snapshot["stats"]["audio_segments"] >= 1
