"""Camera manager — state machine for camera lifecycle."""
from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Dict, Optional

from .backends.base import BackendType, Frame, VideoBackend
from .backends.local import LocalBackend
from .backends.teleop import TeleopBackend
from .config import CameraConfig

log = logging.getLogger("cockpit.camera.manager")


class CameraState(str, Enum):
    STOPPED = "stopped"
    DISABLED = "disabled"
    LOCAL = "local"
    RELAY = "relay"
    SWITCHING = "switching"


class CameraManager:
    """Central camera lifecycle manager.

    Responsibilities:
    - Track Teleop active/inactive state (polling)
    - Manage exclusive camera access (RealSense is single-connection)
    - Coordinate backend switching
    - Fan out frames to connected streaming clients
    """

    def __init__(self, config: CameraConfig) -> None:
        self._config = config
        self._state = CameraState.STOPPED
        self._active_backend: Optional[VideoBackend] = None
        self._local_backend: Optional[LocalBackend] = None
        self._teleop_backend: Optional[TeleopBackend] = None
        self._frame_subscribers: Dict[str, asyncio.Queue[Frame]] = {}
        self._raw_subscribers: Dict[str, asyncio.Queue[Frame]] = {}
        self._lock = asyncio.Lock()
        self._poll_task: Optional[asyncio.Task] = None
        self._broadcast_task: Optional[asyncio.Task] = None

    @property
    def state(self) -> CameraState:
        return self._state

    @property
    def active_backend_type(self) -> Optional[BackendType]:
        return self._active_backend.backend_type if self._active_backend else None

    @property
    def config(self) -> CameraConfig:
        return self._config

    def status(self) -> Dict[str, Any]:
        return {
            "state": self._state.value,
            "backend": self.active_backend_type.value if self.active_backend_type else None,
            "clients": len(self._frame_subscribers),
            "raw_clients": len(self._raw_subscribers),
        }

    async def start(self) -> None:
        """Start camera manager — try to capture RealSense."""
        from ..bridges.teleop import TELEOP

        if self._state not in (CameraState.STOPPED, CameraState.DISABLED):
            return

        # Check if Teleop is already running
        teleop_running = False
        try:
            teleop_running = await TELEOP.is_running()
        except Exception:
            pass

        if teleop_running:
            log.info("Teleop already running, starting in RELAY mode")
            await self._switch_to_relay()
        else:
            log.info("Starting in LOCAL mode")
            await self._switch_to_local()

        # Start Teleop state polling
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._teleop_poll_loop())

        # Start frame broadcast loop
        if self._broadcast_task is None or self._broadcast_task.done():
            self._broadcast_task = asyncio.create_task(self._broadcast_loop())

    async def stop(self) -> None:
        """Stop camera server -> DISABLED mode (legacy clients can use RealSense)."""
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None
        if self._broadcast_task:
            self._broadcast_task.cancel()
            self._broadcast_task = None

        async with self._lock:
            if self._active_backend and self._active_backend.is_active:
                await self._active_backend.stop()
            self._active_backend = None
            self._local_backend = None
            self._teleop_backend = None
            self._state = CameraState.DISABLED

        log.info("Camera stopped -> DISABLED")

    async def shutdown(self) -> None:
        """Fully stop camera manager."""
        await self.stop()
        self._state = CameraState.STOPPED

    def subscribe(self, client_id: str) -> asyncio.Queue[Frame]:
        q: asyncio.Queue[Frame] = asyncio.Queue(maxsize=10)
        self._frame_subscribers[client_id] = q
        return q

    def unsubscribe(self, client_id: str) -> None:
        self._frame_subscribers.pop(client_id, None)

    def subscribe_raw(self, client_id: str) -> asyncio.Queue[Frame]:
        q: asyncio.Queue[Frame] = asyncio.Queue(maxsize=5)
        self._raw_subscribers[client_id] = q
        return q

    def unsubscribe_raw(self, client_id: str) -> None:
        self._raw_subscribers.pop(client_id, None)

    # --- Internal ---

    async def _teleop_poll_loop(self) -> None:
        """Poll Teleop API to detect state changes."""
        from ..bridges.teleop import TELEOP

        while True:
            try:
                teleop_active = await TELEOP.is_preview_active()

                if teleop_active and self._state == CameraState.LOCAL:
                    await self._switch_to_relay()
                elif not teleop_active and self._state == CameraState.RELAY:
                    await self._switch_to_local()

            except Exception as e:
                log.debug("Teleop poll error: %s", e)

            await asyncio.sleep(self._config.teleop_poll_interval_s)

    async def _switch_to_relay(self) -> None:
        """Switch from local capture to Teleop relay."""
        async with self._lock:
            self._state = CameraState.SWITCHING

            # Stop local backend (releases RealSense)
            if self._local_backend and self._local_backend.is_active:
                await self._local_backend.stop()
                self._local_backend = None

            # Start Teleop relay
            self._teleop_backend = TeleopBackend(self._config)
            await self._teleop_backend.start()
            self._active_backend = self._teleop_backend
            self._state = CameraState.RELAY

            log.info("Camera: switched to RELAY mode (Teleop active)")

    async def _switch_to_local(self) -> None:
        """Switch from Teleop relay to local capture."""
        async with self._lock:
            self._state = CameraState.SWITCHING

            # Stop Teleop backend
            if self._teleop_backend and self._teleop_backend.is_active:
                await self._teleop_backend.stop()
                self._teleop_backend = None

            # Start local capture
            self._local_backend = LocalBackend(self._config)
            try:
                await self._local_backend.start()
            except Exception as e:
                log.error("Failed to start local capture: %s", e)
                self._state = CameraState.DISABLED
                return

            self._active_backend = self._local_backend
            self._state = CameraState.LOCAL

            log.info("Camera: switched to LOCAL mode (Teleop inactive)")

    async def _broadcast_loop(self) -> None:
        """Pull frames from active backend, push to all subscribers."""
        while True:
            if self._active_backend and self._active_backend.is_active:
                async for frame in self._active_backend.frames():
                    for client_id, queue in self._frame_subscribers.items():
                        if queue.full():
                            try:
                                queue.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                        queue.put_nowait(frame)
            else:
                await asyncio.sleep(0.1)
