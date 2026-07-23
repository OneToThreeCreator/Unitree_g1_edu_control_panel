"""Teleop backend — WebSocket relay from Treelogic Teleop."""
from __future__ import annotations

import asyncio
import logging
import threading
import time

import websocket

log = logging.getLogger("cockpit.camera.teleop")


class TeleopBackend:
    """Relay H.265 stream from Teleop's WebSocket preview.

    Uses websocket-client (sync) in a background thread because
    Teleop's RobotAdmin server responds HTTP/1.0.
    """

    def __init__(self, teleop_ws_url: str, codec: str = "h265") -> None:
        self._ws_url = teleop_ws_url
        self._codec = codec
        self._running = False
        self._thread: Optional[threading.Thread] = None

    @property
    def is_active(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._run_ws, daemon=True)
        self._thread.start()
        log.info("TeleopBackend started, connecting to %s", self._ws_url)

    async def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        log.info("TeleopBackend stopped")

    def _run_ws(self) -> None:
        """Background thread: run websocket-client reconnect loop."""
        delay = 1.0
        max_delay = 10.0
        while self._running:
            url = f"{self._ws_url}?codec={self._codec}"

            def on_open(ws):
                nonlocal delay
                delay = 1.0
                log.info("TeleopBackend connected to %s", url)

            def on_message(ws, message):
                # Forward raw H.265 NAL units to GStreamer pipeline
                # TODO: push to GStreamer appsrc
                pass

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
