"""Teleop backend — WebSocket relay from Treelogic Teleop."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import AsyncIterator

import websocket

from .base import BackendType, Frame, VideoBackend
from ..config import CameraConfig

log = logging.getLogger("cockpit.camera.teleop")


class TeleopBackend(VideoBackend):
    """Relay video from Treelogic Teleop's WebSocket preview.

    Uses websocket-client (sync) in a background thread because
    Teleop's RobotAdmin server responds HTTP/1.0 which the async
    websockets library doesn't accept.
    """

    def __init__(self, config: CameraConfig) -> None:
        self._config = config
        self._running = False
        self._frame_queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=10)
        self._ws: websocket.WebSocketApp | None = None
        self._thread: threading.Thread | None = None
        self._codec = config.teleop_codec

    @property
    def backend_type(self) -> BackendType:
        return BackendType.TELEOP

    @property
    def is_active(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run_ws, daemon=True)
        self._thread.start()
        log.info("TeleopBackend started, connecting to %s", self._config.teleop_ws_url)

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        log.info("TeleopBackend stopped")

    async def frames(self) -> AsyncIterator[Frame]:
        while self._running:
            try:
                frame = await asyncio.wait_for(self._frame_queue.get(), timeout=2.0)
                yield frame
            except asyncio.TimeoutError:
                continue

    def _run_ws(self) -> None:
        """Background thread: run websocket-client reconnect loop."""
        delay = 1.0
        max_delay = 10.0
        while self._running:
            url = f"{self._config.teleop_ws_url}?codec={self._codec}"

            def on_open(ws):
                nonlocal delay
                delay = 1.0
                log.info("TeleopBackend connected to %s", url)

            def on_message(ws, message):
                if not self._running:
                    return
                if isinstance(message, bytes) and len(message) > 0:
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
                    log.info("TeleopBackend: frame %d bytes, queue=%d", len(message), self._frame_queue.qsize())

            def on_error(ws, error):
                if self._running:
                    log.warning("TeleopBackend: %s", error)

            def on_close(ws, code, msg):
                pass

            self._ws = websocket.WebSocketApp(
                url,
                on_open=on_open,
                on_message=on_message,
                on_error=on_error,
                on_close=on_close,
            )
            try:
                self._ws.run_forever(ping_interval=0)
            except Exception as e:
                if self._running:
                    log.warning("TeleopBackend run_forever error: %s", e)

            if self._running:
                log.info("TeleopBackend disconnected, retrying in %.0fs", delay)
                time.sleep(delay)
                delay = min(delay * 2, max_delay)
