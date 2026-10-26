"""Trigger-source regression: both emergency triggers are distinguishable.

Mode A (fall) and Mode B (SOS gesture) share one lifecycle, and every
emergency record must retain how it started. The SOS gesture uses the
existing 'manual' TriggerType enum value; its distinguishing marker lives in
``trigger_metadata.trigger_source`` which the start endpoint now always
populates.
"""

from __future__ import annotations

from conftest import start_emergency


def test_fall_emergency_records_fall_source(client, enroll):
    device = enroll()
    response = start_emergency(client, device, trigger="fall")
    assert response.status_code == 200, response.text
    meta = response.json()["emergency"]["trigger_metadata"]
    assert meta["trigger_source"] == "fall"


def test_sos_gesture_records_power_button_source(client, enroll):
    """Mode B: the SOS gesture sends trigger_type=manual + its own source."""
    device = enroll()
    response = start_emergency(
        client, device, trigger="manual",
        trigger_metadata={"gesture": "triple_press", "trigger_source": "power_button"},
    )
    assert response.status_code == 200, response.text
    body = response.json()["emergency"]
    assert body["trigger_type"] == "manual"          # existing enum preserved
    assert body["trigger_metadata"]["trigger_source"] == "power_button"
    assert body["trigger_metadata"]["gesture"] == "triple_press"


def test_on_screen_sos_records_on_screen_source(client, enroll):
    device = enroll()
    response = start_emergency(
        client, device, trigger="manual",
        trigger_metadata={"manual": True, "trigger_source": "on_screen_sos"},
    )
    assert response.status_code == 200, response.text
    meta = response.json()["emergency"]["trigger_metadata"]
    assert meta["trigger_source"] == "on_screen_sos"


def test_sources_do_not_collide(client, enroll):
    """A fall and a gesture SOS cannot be confused in the incident history."""
    device = enroll()
    first = start_emergency(client, device, trigger="fall")
    assert first.status_code == 200
    first_id = first.json()["emergency"]["emergency_id"]

    # The one-active rule: end the fall, then start the gesture SOS.
    client.post(
        f"/api/emergencies/{first_id}/status",
        headers=device["headers"],
        json={"status": "resolved"},
    )
    second = start_emergency(
        client, device, trigger="manual",
        trigger_metadata={"gesture": "triple_press", "trigger_source": "power_button"},
    )
    assert second.status_code == 200

    listing = client.get(
        "/api/dashboard/emergencies", headers={"Authorization": "Bearer test-dashboard-token"}
    ).json() if False else None  # authority fixture not needed: scoped below

    detail = client.get(
        f"/api/emergencies/{first_id}", headers=device["headers"]
    ).json()
    detail2 = client.get(
        f"/api/emergencies/{second.json()['emergency']['emergency_id']}",
        headers=device["headers"],
    ).json()
    sources = {
        detail["trigger_metadata"]["trigger_source"],
        detail2["trigger_metadata"]["trigger_source"],
    }
    assert sources == {"fall", "power_button"}
