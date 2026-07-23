"""Camera manager — state machine for camera lifecycle."""
from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import Any, Dict, Optional

from .config import CameraConfig
from .teleop import TeleopBackend

log = logging.getLogger("cockpit.camera.manager")


class CameraState(str, Enum):
    STOPPED = "stopped"
    DISABLED = "disabled"
    LOCAL = "local"
    RELAY = "relay"
    SWITCHING = "switching"


class CameraManager:
    """Camera lifecycle manager.

    State machine: STOPPED → DISABLED → LOCAL ↔ RELAY → STOPPED

    - LOCAL: Rust bridge captures from RealSense, GStreamer encodes H.265
    - RELAY: Teleop captures RealSense, we relay H.265 stream
    - DISABLED: No camera server, legacy clients can use RealSense directly
    """

    def __init__(self, config: CameraConfig, teleop_bridge: object = None) -> None:
        self._config = config
        self._teleop = teleop_bridge
        self._state = CameraState.STOPPED
        self._poll_task: Optional[asyncio.Task] = None

    @property
    def state(self) -> CameraState:
        return self._state

    @property
    def active_backend_type(self) -> Optional[str]:
        if self._state == CameraState.LOCAL:
            return "local"
        if self._state == CameraState.RELAY:
            return "teleop"
        return None

    @property
    def config(self) -> CameraConfig:
        return self._config

    def status(self) -> Dict[str, Any]:
        return {
            "state": self._state.value,
            "backend": self.active_backend_type,
        }

    async def start(self) -> None:
        """Start camera manager."""
        if self._state not in (CameraState.STOPPED, CameraState.DISABLED):
            return

        # Check if Teleop is already running
        teleop_running = False
        if self._teleop:
            try:
                teleop_running = await self._teleop.is_running()
            except Exception:
                pass

        if teleop_running:
            log.info("Teleop already running → RELAY mode")
            self._state = CameraState.RELAY
        else:
            log.info("Starting LOCAL mode")
            self._state = CameraState.LOCAL

        # Start Teleop state polling
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._teleop_poll_loop())

        # TODO: Start Rust bridge + GStreamer pipeline

    async def stop(self) -> None:
        """Stop camera server → DISABLED."""
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

        self._state = CameraState.DISABLED
        log.info("Camera stopped → DISABLED")

        # TODO: Stop Rust bridge + GStreamer pipeline

    async def shutdown(self) -> None:
        await self.stop()
        self._state = CameraState.STOPPED

    async def snapshot_jpeg(self) -> Optional[bytes]:
        # TODO: Get from GStreamer appsink
        return None

    async def _teleop_poll_loop(self) -> None:
        if not self._teleop:
            return
        while True:
            try:
                teleop_active = await self._teleop.is_running()
                if teleop_active and self._state == CameraState.LOCAL:
                    log.info("Teleop detected → RELAY mode")
                    self._state = CameraState.RELAY
                    # TODO: Switch GStreamer pipeline to relay mode
                elif not teleop_active and self._state == CameraState.RELAY:
                    log.info("Teleop stopped → LOCAL mode")
                    self._state = CameraState.LOCAL
                    # TODO: Switch GStreamer pipeline to local mode
            except Exception as e:
                log.debug("Teleop poll error: %s", e)
            await asyncio.sleep(self._config.teleop.poll_interval)
