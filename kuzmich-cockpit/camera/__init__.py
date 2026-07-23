"""Camera module — FastAPI router + initialization.

WebSocket endpoints (raw BGR, depth) are served by GStreamer natively
via `websocketserver` elements on ports 8082/8083. Python only handles
REST API and WebRTC signaling.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Response

from .config import CameraConfig
from .manager import CameraManager

log = logging.getLogger("cockpit.camera")

router = APIRouter(prefix="/api/camera", tags=["camera"])

_camera_manager: Optional[CameraManager] = None


def init_camera(config: CameraConfig, teleop_bridge: object = None) -> None:
    global _camera_manager
    _camera_manager = CameraManager(config, teleop_bridge=teleop_bridge)
    log.info("Camera module initialized")


def get_camera_manager() -> Optional[CameraManager]:
    return _camera_manager


# --- REST endpoints ---


@router.get("/status")
async def camera_status():
    if _camera_manager is None:
        return {"state": "stopped", "backend": None}
    return _camera_manager.status()


@router.put("/start")
async def camera_start():
    if _camera_manager is None:
        raise HTTPException(503, "Camera module not initialized")
    await _camera_manager.start()
    return {"status": "started", **_camera_manager.status()}


@router.put("/stop")
async def camera_stop():
    if _camera_manager is None:
        raise HTTPException(503, "Camera module not initialized")
    await _camera_manager.stop()
    return {"status": "stopped", **_camera_manager.status()}


@router.get("/snapshot.jpg")
async def camera_snapshot():
    if _camera_manager is None:
        return Response(status_code=503, content=b"camera module not initialized")
    frame = await _camera_manager.snapshot_jpeg()
    if frame:
        return Response(
            content=frame,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )
    return Response(status_code=503, content=b"no frame available")


# NOTE: WebSocket endpoints for MJPEG, raw BGR, depth are proxied through FastAPI
# (port 8080) because browser CSP blocks cross-origin WebSocket connections.
# GStreamer websocketsink runs on separate ports (8082, 8083, 8084).
# Python proxies WebSocket data from GStreamer to clients on port 8080.


# --- WebRTC signaling ---


@router.post("/webrtc/offer")
async def webrtc_offer(data: Dict[str, Any]):
    """WebRTC SDP offer/answer exchange."""
    if _camera_manager is None:
        raise HTTPException(503, "Camera module not initialized")
    # TODO: implement GStreamer webrtcbin signaling
    return {"error": "WebRTC not yet implemented", "hint": "Use MJPEG fallback"}


# --- WebSocket proxies (port 8080) ---
# Browser CSP blocks cross-origin WebSocket to GStreamer ports (8082-8084).
# Python proxies WebSocket data from GStreamer websocketsink to browser clients.

@router.websocket("/ws/mjpeg")
async def ws_mjpeg_proxy(ws: WebSocket):
    """Proxy MJPEG from GStreamer websocketsink:8084 to browser."""
    await ws.accept()
    try:
        import websockets
        async with websockets.connect(f"ws://127.0.0.1:{_camera_manager.config.ws_raw_bgr_port + 2}") as gst_ws:
            async for msg in gst_ws:
                if isinstance(msg, bytes):
                    await ws.send_bytes(msg)
    except Exception as e:
        log.warning("MJPEG proxy error: %s", e)
        try:
            await ws.close()
        except Exception:
            pass


@router.websocket("/ws/raw")
async def ws_raw_proxy(ws: WebSocket):
    """Proxy raw BGR from GStreamer websocketsink:8082 to browser."""
    await ws.accept()
    try:
        import websockets
        async with websockets.connect(f"ws://127.0.0.1:{_camera_manager.config.ws_raw_bgr_port}") as gst_ws:
            async for msg in gst_ws:
                if isinstance(msg, bytes):
                    await ws.send_bytes(msg)
    except Exception as e:
        log.warning("Raw BGR proxy error: %s", e)
        try:
            await ws.close()
        except Exception:
            pass


@router.websocket("/ws/depth")
async def ws_depth_proxy(ws: WebSocket):
    """Proxy depth Z16 from GStreamer websocketsink:8083 to browser."""
    await ws.accept()
    try:
        import websockets
        async with websockets.connect(f"ws://127.0.0.1:{_camera_manager.config.ws_depth_port}") as gst_ws:
            async for msg in gst_ws:
                if isinstance(msg, bytes):
                    await ws.send_bytes(msg)
    except Exception as e:
        log.warning("Depth proxy error: %s", e)
        try:
            await ws.close()
        except Exception:
            pass
