"""
Realtime fan-out hub
====================

Two independent realtime channels, both keyed by ``emergency_id`` so that data
can never leak across emergencies:

``/ws/dashboard``
    Authority dashboard socket.  Receives JSON control messages
    (``emergency.created``, ``emergency.updated``, ``event.added``,
    ``audio.segment``, ``live_audio.state`` ...).

``/ws/device/{emergency_id}``
    The phone's socket.  Pushes location / telemetry / events and raw live
    PCM audio frames.

``/ws/live-audio/{emergency_id}``
    Dashboard listeners for one emergency.  Raw PCM frames received from the
    device of *that* emergency are relayed verbatim; frames are never fanned
    out to other emergencies' listeners.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional, Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._dashboard: Set[WebSocket] = set()
        # device sockets: emergency_id -> socket
        self._device_sockets: Dict[str, WebSocket] = {}
        # live audio listeners: emergency_id -> sockets
        self._audio_listeners: Dict[str, Set[WebSocket]] = {}
        # live audio producers: emergency_id -> socket
        self._audio_producers: Dict[str, WebSocket] = {}
        self._lock = asyncio.Lock()

        # simple counters for observability / dashboard badges
        self.stats = {
            "dashboard_broadcasts": 0,
            "live_audio_frames": 0,
            "live_audio_bytes": 0,
            "dropped_sockets": 0,
        }

    # ------------------------------------------------------------------
    # Dashboard control channel
    # ------------------------------------------------------------------
    async def connect_dashboard(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._dashboard.add(websocket)
        logger.info("Dashboard socket connected (%d total)", len(self._dashboard))

    async def disconnect_dashboard(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._dashboard.discard(websocket)
        logger.info("Dashboard socket disconnected (%d left)", len(self._dashboard))

    async def broadcast_dashboard(self, message: dict) -> None:
        """Send a JSON message to every connected dashboard."""
        async with self._lock:
            targets = list(self._dashboard)
        dead = []
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        self.stats["dashboard_broadcasts"] += 1
        if dead:
            async with self._lock:
                for ws in dead:
                    self._dashboard.discard(ws)
            self.stats["dropped_sockets"] += len(dead)

    # ------------------------------------------------------------------
    # Device channel
    # ------------------------------------------------------------------
    async def connect_device(self, emergency_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            previous = self._device_sockets.get(emergency_id)
            self._device_sockets[emergency_id] = websocket
        if previous is not None and previous is not websocket:
            # A newer session for the same emergency replaced the old socket.
            try:
                await previous.close(code=4000, reason="replaced by newer device session")
            except Exception:
                pass
        logger.info("Device socket connected for emergency %s", emergency_id)

    async def disconnect_device(self, emergency_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            if self._device_sockets.get(emergency_id) is websocket:
                self._device_sockets.pop(emergency_id, None)
        logger.info("Device socket disconnected for emergency %s", emergency_id)

    async def send_to_device(self, emergency_id: str, message: dict) -> bool:
        async with self._lock:
            ws = self._device_sockets.get(emergency_id)
        if not ws:
            return False
        try:
            await ws.send_json(message)
            return True
        except Exception:
            await self.disconnect_device(emergency_id, ws)
            return False

    def has_device_socket(self, emergency_id: str) -> bool:
        return emergency_id in self._device_sockets

    # ------------------------------------------------------------------
    # Live audio relay (strictly per emergency_id)
    # ------------------------------------------------------------------
    async def connect_listener(self, emergency_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            self._audio_listeners.setdefault(emergency_id, set()).add(websocket)
        logger.info(
            "Live-audio listener joined emergency %s (%d listening)",
            emergency_id,
            len(self._audio_listeners.get(emergency_id, ())),
        )

    async def disconnect_listener(self, emergency_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            listeners = self._audio_listeners.get(emergency_id)
            if listeners:
                listeners.discard(websocket)
                if not listeners:
                    self._audio_listeners.pop(emergency_id, None)

    def listener_count(self, emergency_id: str) -> int:
        return len(self._audio_listeners.get(emergency_id, ()))

    def audio_streaming(self, emergency_id: str) -> bool:
        return emergency_id in self._audio_producers

    async def set_audio_producing(self, emergency_id: str, websocket: WebSocket, producing: bool) -> None:
        async with self._lock:
            if producing:
                self._audio_producers[emergency_id] = websocket
            elif self._audio_producers.get(emergency_id) is websocket:
                self._audio_producers.pop(emergency_id, None)

    async def relay_audio(self, emergency_id: str, frame: bytes) -> int:
        """Relay one PCM frame to listeners of this emergency only.

        Returns the number of listeners that received the frame.
        """
        async with self._lock:
            listeners = list(self._audio_listeners.get(emergency_id, ()))
        if not listeners:
            return 0

        delivered = 0
        dead = []
        for ws in listeners:
            try:
                await ws.send_bytes(frame)
                delivered += 1
            except Exception:
                dead.append(ws)
        if delivered:
            self.stats["live_audio_frames"] += 1
            self.stats["live_audio_bytes"] += len(frame)
        if dead:
            async with self._lock:
                listeners_set = self._audio_listeners.get(emergency_id)
                if listeners_set:
                    for ws in dead:
                        listeners_set.discard(ws)
                    if not listeners_set:
                        self._audio_listeners.pop(emergency_id, None)
            self.stats["dropped_sockets"] += len(dead)
        return delivered

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        return {
            "dashboard_sockets": len(self._dashboard),
            "device_sockets": len(self._device_sockets),
            "live_audio_emergencies": len(self._audio_producers),
            "live_audio_listeners": sum(len(v) for v in self._audio_listeners.values()),
            **self.stats,
        }


manager = ConnectionManager()
