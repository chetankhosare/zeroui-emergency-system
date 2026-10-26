"""
Phone simulator — end-to-end smoke test / demo driver
=====================================================

Acts exactly like the Android app against a running backend:

1. ``GET  /api/config``                      — read the timings
2. ``POST /api/devices/register``            — enroll, keep the device token
3. ``POST /api/emergencies/start``           — activate (one active per user)
4. ``WS   /ws/device/{id}``                  — heartbeats, location, telemetry,
                                               events and live PCM frames
5. ``POST /api/emergencies/{id}/audio/segments`` — a real WAV segment
6. ``POST /api/emergencies/{id}/status``     — resolved / cancelled

Usage (with the backend already running):

    python scripts/device_simulator.py --user demo_phone_001
    python scripts/device_simulator.py --user demo_phone_001 --seconds 45 --keep-active
    python scripts/device_simulator.py --resolve-only <emergency_id>

It never fabricates coordinates: pass ``--lat/--lng`` to place the "phone"
somewhere specific (defaults to Chhatrapati Sambhajinagar, India, which is
inside DIGIPIN coverage).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import struct
import sys
import time
import uuid
from typing import Optional

import httpx

# Windows consoles often default to cp1252; make the log output UTF-8 safe.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

try:  # optional: only needed for the live-audio part of the run
    from websockets.asyncio.client import connect as ws_connect
except Exception:  # pragma: no cover
    try:
        from websockets.client import connect as ws_connect  # type: ignore
    except Exception:
        ws_connect = None  # type: ignore


def build_wav(seconds: float = 5.0, sample_rate: int = 16000, frequency: int = 440) -> bytes:
    """A real 16-bit mono WAV file (a quiet sine tone) — not silence padding."""
    frames = int(sample_rate * seconds)
    pcm = bytearray()
    for index in range(frames):
        value = int(4000 * math.sin(2 * math.pi * frequency * index / sample_rate))
        pcm += struct.pack("<h", value)
    data = bytes(pcm)
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
    header += b"data" + struct.pack("<I", len(data))
    return header + data


def pcm_frames(seconds: float = 2.0, sample_rate: int = 16000) -> list[bytes]:
    """Small PCM16 mono frames exactly like the phone streams them."""
    raw = build_wav(seconds, sample_rate)[44:]
    chunk = sample_rate // 5 * 2  # 200 ms frames
    return [raw[i:i + chunk] for i in range(0, len(raw), chunk)]


class PhoneSimulator:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.base = args.base.rstrip("/")
        self.token: Optional[str] = None
        self.emergency_id: Optional[str] = None
        self.digipin: Optional[str] = None
        self.client = httpx.Client(base_url=self.base, timeout=20.0)

    # ── helpers ────────────────────────────────────────────────────────
    def headers(self, enrolled: bool = True) -> dict:
        headers = {"Content-Type": "application/json"}
        if enrolled and self.token:
            headers["X-Device-Token"] = self.token
        return headers

    def log(self, message: str) -> None:
        print(f"[phone] {message}", flush=True)

    # ── steps ──────────────────────────────────────────────────────────
    def read_config(self) -> dict:
        config = self.client.get("/api/config").json()
        self.log(
            f"config: countdown={config['countdown_seconds']}s "
            f"location={config['location_interval_seconds']}s "
            f"segment={config['audio_segment_seconds']}s"
        )
        return config

    def enroll(self) -> None:
        response = self.client.post(
            "/api/devices/register",
            headers={"X-Enrollment-Token": self.args.enroll_token, "Content-Type": "application/json"},
            json={
                "user_id": self.args.user,
                "display_name": f"{self.args.user} (python simulator)",
                "platform": "python-simulator",
                "manufacturer": "Python",
                "model": "device_simulator",
                "os_version": "3.12",
                "app_version": "2.0.0-sim",
                "capabilities": {
                    "accelerometer": True,
                    "gyroscope": False,
                    "gps": True,
                    "microphone": True,
                    "simulated_device": True,
                },
            },
        )
        response.raise_for_status()
        body = response.json()
        self.token = body["device_token"]
        self.log(f"enrolled device {body['device_id']} for user {body['user_id']}")

    def start_emergency(self, config: dict, keep_active: bool) -> None:
        location = {
            "latitude": self.args.lat,
            "longitude": self.args.lng,
            "accuracy_m": 7.5,
            "provider": "simulated-gps",
        }
        payload = {
            "trigger_type": self.args.trigger,
            "latitude": location["latitude"],
            "longitude": location["longitude"],
            "accuracy_m": location["accuracy_m"],
            "location_provider": location["provider"],
            "countdown_seconds": config["countdown_seconds"],
            "trigger_metadata": {
                "impact_magnitude": 29.7,
                "freefall_ms": 240,
                "inactivity_ms": 2600,
                "source": "device_simulator.py",
            },
            "capabilities": {"accelerometer": True, "gps": True, "microphone": True},
            "device": {"platform": "python-simulator", "model": "device_simulator"},
            "client_emergency_id": f"sim-{uuid.uuid4().hex[:12]}",
            "event_logs": [
                {"event_type": "fall_detected", "description": "Impact 29.7 m/s² then 2.6 s stillness"},
                {"event_type": "countdown_started", "description": "15 s cancel window started"},
            ],
        }
        response = self.client.post("/api/emergencies/start", headers=self.headers(), json=payload)
        if response.status_code == 409:
            detail = response.json()["detail"]
            self.log(f"user already has an active emergency ({detail['emergency_id']}) — adopting it")
            self.emergency_id = detail["emergency_id"]
            return
        response.raise_for_status()
        body = response.json()
        emergency = body["emergency"]
        self.emergency_id = emergency["emergency_id"]
        self.digipin = emergency.get("digipin")
        self.log(
            f"EMERGENCY {self.emergency_id} activated · "
            f"digipin={self.digipin or 'unavailable'} · sms={body.get('sms_sent')}/{body.get('sms_total')}"
        )

    def post(self, path: str, payload: dict) -> dict:
        response = self.client.post(path, headers=self.headers(), json=payload)
        response.raise_for_status()
        return response.json()

    def send_location(self, drift: float = 0.0004) -> None:
        payload = {
            "latitude": self.args.lat + drift * math.sin(time.time() / 20),
            "longitude": self.args.lng + drift * math.cos(time.time() / 20),
            "accuracy_m": 6.0,
            "provider": "simulated-gps",
            "speed_mps": 1.2,
        }
        body = self.post(f"/api/emergencies/{self.emergency_id}/locations", payload)
        self.digipin = body.get("digipin") or self.digipin
        self.log(f"location → digipin {self.digipin}")

    def send_telemetry(self, live_audio: bool) -> None:
        self.post(
            f"/api/emergencies/{self.emergency_id}/telemetry",
            {
                "battery_pct": 73,
                "network_type": "wifi",
                "connectivity": "online",
                "app_state": "foreground_service",
                "foreground_service": True,
                "screen_on": False,
                "audio_session_active": live_audio,
                "audio_segment_sequence": 1,
                "motion_state": "stationary",
                "accelerometer_magnitude": 9.81,
                "sensors": {"accelerometer": True, "gps": True, "microphone": True},
            },
        )
        self.log("telemetry sent")

    def upload_segment(self) -> None:
        wav = build_wav(self.args.segment_seconds)
        response = self.client.post(
            f"/api/emergencies/{self.emergency_id}/audio/segments",
            headers={"X-Device-Token": self.token or ""},
            files={"file": (f"segment_0001.wav", wav, "audio/wav")},
            data={
                "sequence": "1",
                "duration_seconds": str(self.args.segment_seconds),
                "sample_rate": "16000",
                "channels": "1",
                "codec": "pcm_s16le",
            },
        )
        response.raise_for_status()
        segment = response.json()["segment"]
        self.log(f"audio segment #{segment['sequence']} stored ({segment['size_bytes']} bytes)")

    async def stream_socket(self, seconds: float) -> None:
        if ws_connect is None:
            self.log("websockets library unavailable — skipping the realtime part")
            return
        url = f"{self.base.replace('http', 'ws', 1)}/ws/device/{self.emergency_id}?token={self.token}"
        try:
            async with ws_connect(url, open_timeout=10) as socket:
                ready = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
                self.log(f"realtime ready: {ready.get('type')} (live audio → {ready['live_audio']['endpoint']})")

                await socket.send(json.dumps({"type": "audio_state", "streaming": True,
                                              "format": {"encoding": "pcm_s16le", "sample_rate": 16000, "channels": 1}}))
                frames = pcm_frames(min(seconds, 2.0))
                for frame in frames:
                    await socket.send(frame)
                    await asyncio.sleep(0.2)

                await socket.send(json.dumps({"type": "heartbeat"}))
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    try:
                        message = json.loads(await asyncio.wait_for(socket.recv(), timeout=5))
                    except asyncio.TimeoutError:
                        break
                    if message.get("type") == "ack":
                        self.log(f"socket ack: {message.get('kind')}")
                    if message.get("type") == "error":
                        self.log(f"socket error: {message.get('detail')}")
                await socket.send(json.dumps({"type": "audio_state", "streaming": False}))
        except Exception as error:  # pragma: no cover - demo path
            self.log(f"realtime channel failed: {error}")

    def end_emergency(self, status: str, reason: str | None = None) -> None:
        body = {"status": status, "note": "Ended by device_simulator.py"}
        if status == "cancelled":
            body.update({"pin_verified": True, "reason": reason or "user_confirmed_safe"})
            if self.args.pin:
                body["pin"] = self.args.pin
        response = self.client.post(
            f"/api/emergencies/{self.emergency_id}/status", headers=self.headers(), json=body
        )
        if response.status_code == 403:
            self.log("cancel rejected (PIN required/correct) — leaving the emergency active")
            return
        response.raise_for_status()
        self.log(f"emergency {status}: {response.json()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Act as a Zero UI phone against a running backend")
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="backend base URL")
    parser.add_argument("--user", default=f"sim_user_{uuid.uuid4().hex[:6]}", help="user id")
    parser.add_argument("--enroll-token", default="zeroui-dev-enroll-token")
    parser.add_argument("--trigger", default="fall", choices=["fall", "manual", "shake", "test"])
    parser.add_argument("--lat", type=float, default=19.8762, help="latitude (inside DIGIPIN coverage)")
    parser.add_argument("--lng", type=float, default=75.3433)
    parser.add_argument("--seconds", type=int, default=20, help="how long to keep sending updates")
    parser.add_argument("--segment-seconds", type=float, default=60.0)
    parser.add_argument("--keep-active", action="store_true", help="do not resolve at the end")
    parser.add_argument("--cancel", action="store_true", help="cancel with the PIN instead of resolving")
    parser.add_argument("--pin", default="1234", help="safety PIN used with --cancel")
    parser.add_argument("--resolve-only", metavar="EMERGENCY_ID", help="resolve an existing emergency and exit")
    args = parser.parse_args()

    phone = PhoneSimulator(args)

    if args.resolve_only:
        phone.emergency_id = args.resolve_only
        phone.enroll()
        phone.end_emergency("resolved")
        return 0

    config = phone.read_config()
    phone.enroll()
    phone.start_emergency(config, args.keep_active)
    if phone.emergency_id is None:
        return 1

    phone.send_telemetry(live_audio=True)
    phone.send_location()
    asyncio.run(phone.stream_socket(min(args.seconds, 4)))
    phone.upload_segment()
    if args.pin:
        try:
            response = phone.client.post(
                "/api/pin/set",
                headers=phone.headers(),
                json={"user_id": args.user, "pin": args.pin},
            )
            if response.status_code == 200:
                phone.log(f"safety PIN ready for user {args.user}")
        except Exception:
            pass

    interval = config["location_interval_seconds"]
    deadline = time.time() + args.seconds
    tick = 0
    while time.time() < deadline:
        tick += 1
        phone.send_location()
        phone.send_telemetry(live_audio=tick % 2 == 0)
        time.sleep(min(interval, max(3, args.seconds / 4)))

    if args.keep_active:
        phone.log("leaving the emergency ACTIVE (--keep-active)")
    elif args.cancel:
        phone.end_emergency("cancelled")
    else:
        phone.end_emergency("resolved")
    return 0


if __name__ == "__main__":
    sys.exit(main())
