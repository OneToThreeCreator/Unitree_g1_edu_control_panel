"""kuzmich_camera — Python client for camera server.

For YOLO and other local consumers that need raw BGR frames.

Usage:
    from camera.client import CameraClient
    cam = CameraClient()
    jpeg = cam.snapshot()
    for color, meta in cam.stream_raw_bgr():
        model(color)
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterator, Optional, Tuple

import httpx


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
        from pathlib import Path
        data = self.snapshot()
        if data:
            Path(path).write_bytes(data)
            return True
        return False

    def stream_raw_bgr(self, depth: bool = False) -> Iterator[Tuple]:
        """Stream raw BGR frames via WebSocket.

        Returns (color_ndarray, metadata) or (color_ndarray, depth_ndarray, metadata).
        If depth is requested but unavailable, returns color-only with depth_available=False.
        """
        import numpy as np
        import websocket

        url = f"ws://127.0.0.1:8080/api/camera/ws/raw{'?depth=true' if depth else ''}"
        result = [None, None]

        def on_message(ws, msg):
            result[0] = msg

        ws = websocket.WebSocketApp(url, on_message=on_message)
        # Use raw socket approach for iterator
        import socket as _sock
        import base64, os as _os

        host, port = "127.0.0.1", 8080
        path = f"/api/camera/ws/raw{'?depth=true' if depth else ''}"
        s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        s.settimeout(30)
        s.connect((host, port))
        key = base64.b64encode(_os.urandom(16)).decode()
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        s.send(req.encode())
        resp = s.recv(4096)
        if b"101" not in resp:
            s.close()
            raise ConnectionError(f"WebSocket upgrade failed: {resp[:200]}")
        try:
            while True:
                data = s.recv(65536)
                if not data:
                    break
                offset = 0
                while offset < len(data):
                    if offset + 2 > len(data):
                        break
                    byte1 = data[offset]
                    byte2 = data[offset + 1]
                    opcode = byte1 & 0x0F
                    payload_len = byte2 & 0x7F
                    offset += 2
                    if payload_len == 126:
                        payload_len = int.from_bytes(data[offset:offset+2], "big")
                        offset += 2
                    elif payload_len == 127:
                        payload_len = int.from_bytes(data[offset:offset+8], "big")
                        offset += 8
                    masked = bool(byte2 & 0x80)
                    if masked:
                        mask = data[offset:offset+4]
                        offset += 4
                    payload = data[offset:offset+payload_len]
                    offset += payload_len
                    if masked:
                        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
                    if opcode == 0x1:  # text frame (JSON metadata)
                        try:
                            meta = json.loads(payload.decode())
                            if "width" in meta:
                                w, h = meta["width"], meta["height"]
                                depth_avail = meta.get("depth_available", False)
                        except Exception:
                            pass
                    elif opcode == 0x2:  # binary frame (BGR data)
                        if 'w' in dir():
                            color_size = w * h * 3
                            color = np.frombuffer(payload[:color_size], dtype=np.uint8).reshape(h, w, 3)
                            if depth and depth_avail and "depth_width" in meta:
                                dw, dh = meta["depth_width"], meta["depth_height"]
                                depth_data = np.frombuffer(
                                    payload[color_size:], dtype=np.uint16
                                ).reshape(dh, dw)
                                yield color, depth_data, meta
                            else:
                                yield color, meta
                    elif opcode == 0x8:  # close
                        return
        finally:
            s.close()
