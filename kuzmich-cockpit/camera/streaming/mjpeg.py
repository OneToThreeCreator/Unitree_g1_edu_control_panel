"""MJPEG fallback streamer."""
from __future__ import annotations

from typing import AsyncIterator

from ..backends.base import Frame
from ..manager import CameraManager


class MjpegStreamer:
    """MJPEG multipart stream for compatibility with legacy clients."""

    def __init__(self, camera_manager: CameraManager) -> None:
        self._camera = camera_manager

    async def stream(self, client_id: str) -> AsyncIterator[bytes]:
        """Generate MJPEG stream for an HTTP client."""
        queue = self._camera.subscribe(client_id)
        try:
            while True:
                frame = await queue.get()
                if frame.format == "jpeg":
                    header = (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                    ) + f"Content-Length: {len(frame.data)}\r\n\r\n".encode()
                    yield header + frame.data + b"\r\n"
        finally:
            self._camera.unsubscribe(client_id)
