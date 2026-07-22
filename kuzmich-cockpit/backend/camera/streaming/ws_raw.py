"""WebSocket raw BGR frames delivery (for YOLO, etc.)."""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from ..manager import CameraManager

log = logging.getLogger("cockpit.camera.ws_raw")


class WsRawStreamer:
    """WebSocket raw BGR frame delivery for local consumers (YOLO, etc.)."""

    def __init__(self, camera_manager: CameraManager) -> None:
        self._camera = camera_manager

    async def handle(self, ws: WebSocket, depth: bool = False) -> None:
        """WebSocket handler — sends raw BGR frames as binary messages."""
        client_id = f"raw-{id(ws)}"
        queue = self._camera.subscribe_raw(client_id)
        try:
            await ws.accept()

            # Check if depth is available
            depth_available = (
                depth
                and self._camera.state.value == "local"
                and self._camera.config.depth_enabled
            )

            # Send metadata as first JSON message
            meta = {
                "width": self._camera.config.color_width,
                "height": self._camera.config.color_height,
                "format": "bgr",
                "fps": self._camera.config.color_fps,
                "depth_available": depth_available,
            }
            if depth_available:
                meta["depth_width"] = self._camera.config.depth_width
                meta["depth_height"] = self._camera.config.depth_height
                meta["depth_format"] = "z16"
            elif depth:
                meta["depth_reason"] = (
                    "relay_mode" if self._camera.state.value == "relay" else "disabled"
                )

            await ws.send_json(meta)

            while True:
                frame = await queue.get()
                if frame.format == "bgr":
                    data = frame.data
                    if depth_available and frame.depth:
                        data += frame.depth
                    await ws.send_bytes(data)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.debug("WebSocket raw client disconnected: %s", e)
        finally:
            self._camera.unsubscribe_raw(client_id)
