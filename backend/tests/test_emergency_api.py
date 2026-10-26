"""Critical emergency flow: enrolment → activation → updates → end."""

from __future__ import annotations

import io

from conftest import ENROLLMENT_TOKEN


def test_registration_requires_the_enrollment_token(client):
    response = client.post("/api/devices/register", json={"user_id": "u1"})
    assert response.status_code == 403

    response = client.post(
        "/api/devices/register",
        headers={"X-Enrollment-Token": "wrong"},
        json={"user_id": "u1"},
    )
    assert response.status_code == 403


def test_device_endpoints_reject_missing_or_bad_token(client):
    payload = {"trigger_type": "manual", "latitude": 19.87, "longitude": 75.34}
    assert client.post("/api/emergencies/start", json=payload).status_code == 401
    assert (
        client.post(
            "/api/emergencies/start", json=payload, headers={"X-Device-Token": "nope"}
        ).status_code
        == 401
    )


def test_start_requires_valid_coordinates(client, enroll):
    device = enroll()
    response = client.post(
        "/api/emergencies/start",
        headers=device["headers"],
        json={"trigger_type": "fall", "latitude": 200, "longitude": 75.34},
    )
    assert response.status_code == 422


def test_full_lifecycle_reaches_the_dashboard(client, enroll, authority, start):
    device = enroll()

    # 1. Activation after the 15 s countdown
    emergency = start(device)
    emergency_id = emergency["emergency_id"]
    assert emergency["status"] == "emergency"
    assert emergency["connection_state"] == "live"
    assert emergency["digipin"] == "4C3-FJ6-PLK3"
    assert emergency["trigger_type"] == "fall"
    assert emergency["device"]["model"] == "TestModel"
    # the phone's local detection trail is replayed for the authority
    types = [e["event_type"] for e in emergency["events"]]
    assert "fall_detected" in types and "emergency_activated" in types

    # 2. It appears on the authority dashboard
    listing = client.get("/api/dashboard/emergencies", headers=authority).json()
    assert any(e["emergency_id"] == emergency_id for e in listing["emergencies"])

    # 3. Live location (~30 s cadence on the phone)
    response = client.post(
        f"/api/emergencies/{emergency_id}/locations",
        headers=device["headers"],
        json={
            "latitude": 19.8770,
            "longitude": 75.3440,
            "accuracy_m": 6.2,
            "provider": "gps",
            "speed_mps": 1.4,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["digipin_available"] is True
    assert body["digipin"].startswith("4C3-FJ6")  # real DIGIPIN, same grid cell

    # 4. Telemetry (battery, network, foreground service, sensor availability)
    response = client.post(
        f"/api/emergencies/{emergency_id}/telemetry",
        headers=device["headers"],
        json={
            "battery_pct": 71,
            "network_type": "wifi",
            "app_state": "foreground_service",
            "foreground_service": True,
            "audio_session_active": True,
            "accelerometer_magnitude": 9.81,
            "sensors": {"accelerometer": True, "gyroscope": True, "gps": True},
        },
    )
    assert response.status_code == 200

    # 5. Timeline event
    response = client.post(
        f"/api/emergencies/{emergency_id}/events",
        headers=device["headers"],
        json={"event_type": "mic_started", "description": "Emergency audio capture started"},
    )
    assert response.status_code == 200

    # 6. ~1-minute audio segment
    response = client.post(
        f"/api/emergencies/{emergency_id}/audio/segments",
        headers=device["headers"],
        files={"file": ("segment_0001.wav", io.BytesIO(b"RIFF....WAVEfake-pcm"), "audio/wav")},
        data={
            "sequence": 1,
            "duration_seconds": 60.0,
            "sample_rate": 16000,
            "channels": 1,
            "codec": "pcm_s16le",
        },
    )
    assert response.status_code == 200, response.text
    segment = response.json()["segment"]
    assert segment["sequence"] == 1

    # 7. Detail view carries device info, telemetry, events, locations, audio
    detail = client.get(f"/api/dashboard/emergencies/{emergency_id}", headers=authority).json()
    assert detail["telemetry"]["battery_pct"] == 71
    assert len(detail["locations"]) >= 2
    assert len(detail["audio_segments"]) == 1
    assert detail["has_audio"] is True
    assert detail["device"]["platform"] == "android"

    # 8. Audio bytes are actually served (with range support)
    audio_url = segment["url"]
    audio = client.get(audio_url, headers=authority)
    assert audio.status_code == 200
    assert audio.headers["accept-ranges"] == "bytes"
    partial = client.get(audio_url, headers={**authority, "Range": "bytes=0-3"})
    assert partial.status_code == 206
    assert partial.content == b"RIFF"

    # 9. Resolve and confirm terminal state
    response = client.post(
        f"/api/dashboard/emergencies/{emergency_id}/resolve",
        headers=authority,
        json={"note": "Responders arrived"},
    )
    assert response.status_code == 200 and response.json()["changed"] is True

    final = client.get(f"/api/dashboard/emergencies/{emergency_id}", headers=authority).json()
    assert final["status"] == "resolved"
    assert final["connection_state"] == "ended"
    assert final["ended_by"].startswith("dashboard:")
    assert final["duration_seconds"] is not None

    # 10. A resolved emergency is immutable
    response = client.post(
        f"/api/emergencies/{emergency_id}/locations",
        headers=device["headers"],
        json={"latitude": 19.88, "longitude": 75.35},
    )
    assert response.status_code == 409


def test_only_one_active_emergency_per_user(client, enroll, start):
    device = enroll()
    first = start(device)

    response = client.post(
        "/api/emergencies/start",
        headers=device["headers"],
        json={
            "trigger_type": "manual",
            "latitude": 19.8762,
            "longitude": 75.3433,
            "countdown_seconds": 15,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["emergency_id"] == first["emergency_id"]

    # /active lets the phone recover its state after a restart
    active = client.get("/api/emergencies/active", headers=device["headers"]).json()
    assert active["has_active_emergency"] is True
    assert active["emergency"]["emergency_id"] == first["emergency_id"]

    client.post(
        f"/api/emergencies/{first['emergency_id']}/status",
        headers=device["headers"],
        json={"status": "resolved", "note": "ended by user"},
    )

    # ...and a new emergency is allowed once the previous one ended
    second = start(device)
    assert second["emergency_id"] != first["emergency_id"]


def test_retry_with_same_client_id_is_idempotent(client, enroll):
    device = enroll()
    body = {
        "trigger_type": "fall",
        "latitude": 19.8762,
        "longitude": 75.3433,
        "countdown_seconds": 15,
        "client_emergency_id": "retry-key-1",
    }
    first = client.post("/api/emergencies/start", headers=device["headers"], json=body)
    assert first.status_code == 200

    retry = client.post("/api/emergencies/start", headers=device["headers"], json=body)
    assert retry.status_code == 200
    assert retry.json()["duplicate"] is True
    assert retry.json()["emergency"]["emergency_id"] == first.json()["emergency"]["emergency_id"]


def test_cancel_requires_the_safety_pin(client, enroll, start):
    device = enroll()
    emergency = start(device)
    emergency_id = emergency["emergency_id"]

    client.post(
        "/api/pin/set",
        headers=device["headers"],
        json={"user_id": device["user_id"], "pin": "4821"},
    )

    # no PIN confirmation → refused
    response = client.post(
        f"/api/emergencies/{emergency_id}/status",
        headers=device["headers"],
        json={"status": "cancelled", "reason": "user_safe"},
    )
    assert response.status_code == 403

    # wrong PIN → refused
    response = client.post(
        f"/api/emergencies/{emergency_id}/status",
        headers=device["headers"],
        json={"status": "cancelled", "pin_verified": True, "pin": "0000"},
    )
    assert response.status_code == 403

    # correct PIN → cancelled
    response = client.post(
        f"/api/emergencies/{emergency_id}/status",
        headers=device["headers"],
        json={"status": "cancelled", "pin_verified": True, "pin": "4821",
              "reason": "user_confirmed_safe"},
    )
    assert response.status_code == 200
    assert response.json()["new_status"] == "cancelled"

    detail = client.get(f"/api/emergencies/{emergency_id}", headers=device["headers"]).json()
    assert detail["status"] == "cancelled"
    assert detail["connection_state"] == "ended"

    # the user can start a new emergency afterwards
    assert client.get("/api/emergencies/active", headers=device["headers"]).json()[
        "has_active_emergency"
    ] is False


def test_pin_calls_are_scoped_to_the_enrolled_user(client, enroll):
    device = enroll()
    other = enroll()
    response = client.post(
        "/api/pin/set", headers=other["headers"], json={"user_id": device["user_id"], "pin": "1234"}
    )
    assert response.status_code == 403
    assert client.get(f"/api/pin/status/{device['user_id']}", headers=other["headers"]).status_code == 403


def test_digipin_is_null_outside_india(client, enroll):
    device = enroll(user_id="london_user")
    emergency = client.post(
        "/api/emergencies/start",
        headers=device["headers"],
        json={"trigger_type": "manual", "latitude": 51.5074, "longitude": -0.1278},
    ).json()["emergency"]
    assert emergency["digipin"] is None
    assert emergency["digipin_available"] is False
    assert emergency["latitude"] == 51.5074

    client.post(
        f"/api/emergencies/{emergency['emergency_id']}/status",
        headers=device["headers"],
        json={"status": "resolved"},
    )


def test_config_endpoint_exposes_countdown_and_digipin(client):
    body = client.get("/api/config").json()
    assert body["countdown_seconds"] == 15
    assert body["location_interval_seconds"] == 30
    assert body["audio_segment_seconds"] == 60
    assert body["digipin"]["library_available"] is True
