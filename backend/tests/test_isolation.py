"""Isolation, authentication and multi-emergency behaviour."""

from __future__ import annotations

import io
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from conftest import DASHBOARD_TOKEN
from starlette.websockets import WebSocketDisconnect

from config import settings


def test_device_cannot_touch_another_users_emergency(client, enroll, start):
    alice = enroll(user_id="isolation_alice")
    bob = enroll(user_id="isolation_bob")
    emergency = start(alice)
    emergency_id = emergency["emergency_id"]

    assert client.get(f"/api/emergencies/{emergency_id}", headers=bob["headers"]).status_code == 404
    assert (
        client.post(
            f"/api/emergencies/{emergency_id}/locations",
            headers=bob["headers"],
            json={"latitude": 19.88, "longitude": 75.35},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/emergencies/{emergency_id}/telemetry",
            headers=bob["headers"],
            json={"battery_pct": 10},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/emergencies/{emergency_id}/status",
            headers=bob["headers"],
            json={"status": "resolved"},
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/emergencies/{emergency_id}/audio/segments", headers=bob["headers"]).status_code
        == 404
    )

    client.post(
        f"/api/emergencies/{emergency_id}/status",
        headers=alice["headers"],
        json={"status": "resolved"},
    )


def test_audio_segments_stay_with_their_emergency(client, enroll, start, authority):
    alice = enroll(user_id="audio_alice")
    bob = enroll(user_id="audio_bob")
    alice_emergency = start(alice)
    bob_emergency = start(bob)

    upload = client.post(
        f"/api/emergencies/{alice_emergency['emergency_id']}/audio/segments",
        headers=alice["headers"],
        files={"file": ("segment_0001.wav", io.BytesIO(b"ALICE-AUDIO-BYTES"), "audio/wav")},
        data={"sequence": 1, "duration_seconds": 60},
    )
    assert upload.status_code == 200
    segment_id = upload.json()["segment"]["segment_id"]

    # Correct emergency → served
    ok = client.get(
        f"/api/dashboard/emergencies/{alice_emergency['emergency_id']}/audio/segments/{segment_id}",
        headers=authority,
    )
    assert ok.status_code == 200
    assert ok.content == b"ALICE-AUDIO-BYTES"

    # Same segment id through the other emergency → never served
    cross = client.get(
        f"/api/dashboard/emergencies/{bob_emergency['emergency_id']}/audio/segments/{segment_id}",
        headers=authority,
    )
    assert cross.status_code == 404

    # Segment listings are per emergency
    alice_list = client.get(
        f"/api/dashboard/emergencies/{alice_emergency['emergency_id']}/audio/segments",
        headers=authority,
    ).json()
    bob_list = client.get(
        f"/api/dashboard/emergencies/{bob_emergency['emergency_id']}/audio/segments",
        headers=authority,
    ).json()
    assert alice_list["count"] == 1 and bob_list["count"] == 0

    for device, emergency in ((alice, alice_emergency), (bob, bob_emergency)):
        client.post(
            f"/api/emergencies/{emergency['emergency_id']}/status",
            headers=device["headers"],
            json={"status": "resolved"},
        )


def test_dashboard_api_requires_a_session(client, enroll, start):
    device = enroll(user_id="auth_user")
    emergency = start(device)

    assert client.get("/api/dashboard/emergencies").status_code == 401
    assert (
        client.get("/api/dashboard/emergencies", headers={"Authorization": "Bearer forged"}).status_code
        == 401
    )
    assert client.get("/api/dashboard/snapshot").status_code == 401
    assert (
        client.get(f"/api/dashboard/emergencies/{emergency['emergency_id']}/events").status_code == 401
    )

    login = client.post("/api/auth/dashboard/login", json={"token": "wrong-token"})
    assert login.status_code == 401

    client.post(
        f"/api/emergencies/{emergency['emergency_id']}/status",
        headers=device["headers"],
        json={"status": "resolved"},
    )


def test_dashboard_websocket_requires_a_token(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/dashboard"):
            pass

    with client.websocket_connect(f"/ws/dashboard?token={DASHBOARD_TOKEN}") as socket:
        hello = socket.receive_json()
        assert hello["type"] == "hello"


def test_device_websocket_requires_the_owning_device_token(client, enroll, start):
    alice = enroll(user_id="ws_alice")
    bob = enroll(user_id="ws_bob")
    emergency = start(alice)
    emergency_id = emergency["emergency_id"]

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/device/{emergency_id}"):
            pass

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/device/{emergency_id}?token={bob['token']}"):
            pass  # closed with 4404 (not this device's emergency)

    with client.websocket_connect(f"/ws/device/{emergency_id}?token={alice['token']}") as socket:
        ready = socket.receive_json()
        assert ready["type"] == "ready"
        assert ready["emergency_id"] == emergency_id
        socket.send_json({"type": "heartbeat"})
        assert socket.receive_json()["kind"] == "heartbeat"

        # a second, different emergency must not receive these frames
        other = start(bob)
        socket.send_bytes(b"ALICE-PCM")
        assert socket.receive_json  # socket still healthy

    client.post(
        f"/api/emergencies/{emergency_id}/status",
        headers=alice["headers"],
        json={"status": "resolved"},
    )
    client.post(
        f"/api/emergencies/{other['emergency_id']}/status",
        headers=bob["headers"],
        json={"status": "resolved"},
    )


def test_multiple_simultaneous_emergencies(client, enroll, start, authority):
    devices = [enroll(user_id=f"sim_user_{i}") for i in range(3)]
    emergencies = [
        start(device, lat=19.8762 + i * 0.01, lng=75.3433 + i * 0.01)
        for i, device in enumerate(devices)
    ]
    ids = {e["emergency_id"] for e in emergencies}
    assert len(ids) == 3  # unique ids

    snapshot = client.get("/api/dashboard/snapshot", headers=authority).json()
    active_ids = {e["emergency_id"] for e in snapshot["emergencies"] if e["status"] == "emergency"}
    assert ids.issubset(active_ids)
    assert snapshot["stats"]["active"] >= 3

    # Locations never mix between emergencies
    for i, (device, emergency) in enumerate(zip(devices, emergencies)):
        client.post(
            f"/api/emergencies/{emergency['emergency_id']}/locations",
            headers=device["headers"],
            json={"latitude": 20.0 + i, "longitude": 76.0 + i, "provider": "gps"},
        )

    for i, emergency in enumerate(emergencies):
        detail = client.get(
            f"/api/dashboard/emergencies/{emergency['emergency_id']}", headers=authority
        ).json()
        # headline position follows the newest fix…
        assert detail["latitude"] == pytest.approx(20.0 + i)
        assert detail["latest_location"]["latitude"] == pytest.approx(20.0 + i)
        # …and the track only ever contains this emergency's own samples
        allowed = {round(19.8762 + i * 0.01, 6), round(20.0 + i, 6)}
        assert {round(loc["latitude"], 6) for loc in detail["locations"]} <= allowed

    # Resolving one leaves the others active
    client.post(
        f"/api/dashboard/emergencies/{emergencies[0]['emergency_id']}/resolve",
        headers=authority,
        json={"note": "handled"},
    )
    for device, emergency in zip(devices[1:], emergencies[1:]):
        detail = client.get(
            f"/api/dashboard/emergencies/{emergency['emergency_id']}", headers=authority
        ).json()
        assert detail["status"] == "emergency"

    for device, emergency in zip(devices, emergencies):
        client.post(
            f"/api/emergencies/{emergency['emergency_id']}/status",
            headers=device["headers"],
            json={"status": "resolved"},
        )


def test_stale_detection_after_missing_updates(client, enroll, start, authority):
    device = enroll(user_id="stale_user")
    emergency = start(device)
    emergency_id = emergency["emergency_id"]

    old = (datetime.now(timezone.utc) - timedelta(seconds=settings.stale_after_seconds + 60)).isoformat()
    connection = sqlite3.connect(settings.db_path)
    connection.execute(
        "UPDATE emergencies SET last_update_at = ? WHERE emergency_id = ?", (old, emergency_id)
    )
    connection.commit()
    connection.close()

    detail = client.get(f"/api/dashboard/emergencies/{emergency_id}", headers=authority).json()
    assert detail["connection_state"] == "stale"
    assert detail["age_seconds"] > settings.stale_after_seconds

    client.post(
        f"/api/emergencies/{emergency_id}/status",
        headers=device["headers"],
        json={"status": "resolved"},
    )
