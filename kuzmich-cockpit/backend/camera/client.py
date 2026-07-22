"""kuzmich_camera — клиент видеосервера Кузьмич.

Использование:
    from camera.client import CameraClient

    # Простой захват JPEG-кадров
    cam = CameraClient()
    jpeg = cam.snapshot()           # bytes
    cam.save_snapshot("frame.jpg")

    # Потоковая подписка (H.264 NAL units)
    for nal_data in cam.stream_nal():
        decode_h264(nal_data)

    # Потоковая подписка (MJPEG)
    for jpeg_frame in cam.stream_mjpeg():
        process(jpeg_frame)

    # Поток сырых BGR-кадров (для YOLO)
    for color, meta in cam.stream_raw_bgr():
        model(color)

    # С depth
    for color, depth, meta in cam.stream_raw_bgr(depth=True):
        distance = depth[y, x] * 0.001

    # Async версия
    async for jpeg_frame in cam.astream_mjpeg():
        await process(jpeg_frame)
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple, Union

import httpx

try:
    import websockets.sync.client as wsc_sync
except ImportError:
    wsc_sync = None

try:
    import websockets
except ImportError:
    websockets = None


class CameraClient:
    """Client for Kuzmich camera video server."""

    def __init__(self, base_url: str = "http://127.0.0.1:8080") -> None:
        self._base = base_url.rstrip("/")

    def status(self) -> Dict[str, Any]:
        """Get camera server status."""
        resp = httpx.get(f"{self._base}/api/camera/status", timeout=3.0)
        return resp.json() if resp.status_code == 200 else {}

    def snapshot(self) -> Optional[bytes]:
        """Get a single JPEG frame."""
        resp = httpx.get(f"{self._base}/api/camera/snapshot.jpg", timeout=5.0)
        if resp.status_code == 200:
            return resp.content
        return None

    def save_snapshot(self, path: str) -> bool:
        """Save a JPEG frame to file."""
        data = self.snapshot()
        if data:
            Path(path).write_bytes(data)
            return True
        return False

    def stream_nal(self) -> Iterator[bytes]:
        """Iterator of H.264 NAL units from WebSocket."""
        if wsc_sync is None:
            raise ImportError("websockets package required")
        with wsc_sync.connect(
            f"ws://127.0.0.1:8080/api/camera/ws/stream?codec=h264"
        ) as ws:
            while True:
                msg = ws.recv()
                if isinstance(msg, bytes):
                    yield msg

    def stream_raw_bgr(self, depth: bool = False) -> Iterator[Tuple]:
        """Stream raw BGR frames (for YOLO). Returns (color_ndarray, metadata)
        or (color_ndarray, depth_ndarray, metadata) if depth=True.
        If depth is requested but unavailable, returns color-only with depth_available=False in meta."""
        import numpy as np

        if wsc_sync is None:
            raise ImportError("websockets package required")

        url = f"ws://127.0.0.1:8080/api/camera/ws/raw{'?depth=true' if depth else ''}"
        with wsc_sync.connect(url) as ws:
            meta = json.loads(ws.recv())
            w, h = meta["width"], meta["height"]
            depth_avail = meta.get("depth_available", True)
            while True:
                data = ws.recv()
                if isinstance(data, bytes):
                    color_size = w * h * 3
                    color = np.frombuffer(data[:color_size], dtype=np.uint8).reshape(h, w, 3)
                    if depth and depth_avail and "depth_width" in meta:
                        dw, dh = meta["depth_width"], meta["depth_height"]
                        depth_data = np.frombuffer(
                            data[color_size:], dtype=np.uint16
                        ).reshape(dh, dw)
                        yield color, depth_data, meta
                    else:
                        yield color, meta

    async def asnapshot(self) -> Optional[bytes]:
        """Async version of snapshot."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self._base}/api/camera/snapshot.jpg", timeout=5.0
            )
            if resp.status_code == 200:
                return resp.content
        return None

    async def astream_mjpeg(self):
        """Async MJPEG stream."""
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "GET", f"{self._base}/api/camera/stream.mjpg"
            ) as resp:
                async for chunk in resp.aiter_bytes():
                    yield chunk

    async def astream_nal(self):
        """Async H.264 NAL stream."""
        if websockets is None:
            raise ImportError("websockets package required")
        async with websockets.connect(
            f"ws://127.0.0.1:8080/api/camera/ws/stream?codec=h264"
        ) as ws:
            async for msg in ws:
                if isinstance(msg, bytes):
                    yield msg

    async def astream_raw_bgr(self, depth: bool = False):
        """Async raw BGR stream (for YOLO). depth=True adds depth stream."""
        import numpy as np

        if websockets is None:
            raise ImportError("websockets package required")

        url = f"ws://127.0.0.1:8080/api/camera/ws/raw{'?depth=true' if depth else ''}"
        async with websockets.connect(url) as ws:
            meta = json.loads(await ws.recv())
            w, h = meta["width"], meta["height"]
            depth_avail = meta.get("depth_available", True)
            async for msg in ws:
                if isinstance(msg, bytes):
                    color_size = w * h * 3
                    color = np.frombuffer(msg[:color_size], dtype=np.uint8).reshape(h, w, 3)
                    if depth and depth_avail and "depth_width" in meta:
                        dw, dh = meta["depth_width"], meta["depth_height"]
                        depth_data = np.frombuffer(
                            msg[color_size:], dtype=np.uint16
                        ).reshape(dh, dw)
                        yield color, depth_data, meta
                    else:
                        yield color, meta
