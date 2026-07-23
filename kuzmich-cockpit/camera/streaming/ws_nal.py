"""WebSocket raw NAL delivery — primary streaming protocol (JMuxer/MSE)."""
from __future__ import annotations

import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

from ..manager import CameraManager

log = logging.getLogger("cockpit.camera.ws_nal")


class WsNalStreamer:
    """WebSocket raw H.264/H.265 NAL delivery (like Teleop does for JMuxer)."""

    def __init__(self, camera_manager: CameraManager) -> None:
        self._camera = camera_manager

    async def handle(self, ws: WebSocket, codec: str = "h264") -> None:
        """WebSocket handler — sends NAL units as binary messages."""
        client_id = f"ws-{id(ws)}"
        queue = self._camera.subscribe(client_id)
        try:
            await ws.accept()

            # Wait for first frame to detect actual codec
            frame = await queue.get()
            actual_codec = frame.format if frame.format in ("h264", "h265", "av1") else codec

            # Send codec info as first JSON message
            await ws.send_json({
                "codec": actual_codec,
                "width": frame.width or self._camera.config.color_width,
                "height": frame.height or self._camera.config.color_height,
            })
            await ws.send_bytes(frame.data)

            while True:
                frame = await queue.get()
                if frame.format in ("h264", "h265", "av1"):
                    await ws.send_bytes(frame.data)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.debug("WebSocket NAL client disconnected: %s", e)
        finally:
            self._camera.unsubscribe(client_id)
