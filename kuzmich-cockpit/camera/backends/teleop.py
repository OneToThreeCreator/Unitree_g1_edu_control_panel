"""Teleop backend — WebSocket relay from Treelogic Teleop."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator, Optional

import websockets

from .base import BackendType, Frame, VideoBackend
from ..config import CameraConfig

log = logging.getLogger("cockpit.camera.teleop")


class TeleopBackend(VideoBackend):
    """Relay video from Treelogic Teleop's WebSocket preview."""

    def __init__(self, config: CameraConfig) -> None:
        self._config = config
        self._running = False
        self._frame_queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=10)
        self._ws_connection = None
        self._codec = config.teleop_codec

    @property
    def backend_type(self) -> BackendType:
        return BackendType.TELEOP

    @property
    def is_active(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._connect_loop())
        log.info("TeleopBackend started, connecting to %s", self._config.teleop_ws_url)

    async def stop(self) -> None:
        self._running = False
        if self._ws_connection:
            try:
                await self._ws_connection.close()
            except Exception:
                pass
            self._ws_connection = None
        log.info("TeleopBackend stopped")

    async def frames(self) -> AsyncIterator[Frame]:
        while self._running:
            try:
                frame = await asyncio.wait_for(self._frame_queue.get(), timeout=2.0)
                yield frame
            except asyncio.TimeoutError:
                continue

    async def _connect_loop(self) -> None:
        """Reconnect loop — keeps WebSocket alive."""
        delay = 1.0
        max_delay = 10.0
        while self._running:
            try:
                url = f"{self._config.teleop_ws_url}?codec={self._codec}"
                async with websockets.connect(url, open_timeout=5.0) as ws:
                    self._ws_connection = ws
                    delay = 1.0  # Reset delay on successful connection
                    log.info("TeleopBackend connected to %s", url)

                    async for message in ws:
                        if not self._running:
                            break
                        if isinstance(message, bytes):
                            frame = Frame(
                                data=message,
                                pts_ms=time.monotonic() * 1000,
                                width=0,
                                height=0,
                                format=self._codec,
                            )
                            if self._frame_queue.full():
                                try:
                                    self._frame_queue.get_nowait()
                                except asyncio.QueueEmpty:
                                    pass
                            self._frame_queue.put_nowait(frame)

            except Exception as e:
                if self._running:
                    log.warning("TeleopBackend: failed to connect to %s — %s (%s)",
                                url, e, type(e).__name__)
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, max_delay)
