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


@router.get("/stream.mjpg")
async def camera_stream_mjpeg():
    """MJPEG fallback — proxies from GStreamer websocketserver:8084."""
    if _camera_manager is None:
        return Response(status_code=503, content=b"camera module not initialized")
    import httpx
    from typing import AsyncIterator

    mjpeg_ws_port = 8084  # GStreamer websocketserver MJPEG port

    async def gen() -> AsyncIterator[bytes]:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", f"http://127.0.0.1:{mjpeg_ws_port}/stream.mjpg") as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
        except Exception as e:
            log.warning("MJPEG proxy error: %s", e)

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


# --- WebRTC signaling ---


@router.post("/webrtc/offer")
async def webrtc_offer(data: Dict[str, Any]):
    """WebRTC SDP offer/answer exchange."""
    if _camera_manager is None:
        raise HTTPException(503, "Camera module not initialized")
    # TODO: implement GStreamer webrtcbin signaling
    return {"error": "WebRTC not yet implemented", "hint": "Use /stream.mjpg fallback"}


# NOTE: Raw BGR and depth WebSocket endpoints are served by GStreamer
# directly via `websocketserver` elements:
# - Port 8082: ws://host:8082 — raw BGR frames (for YOLO)
# - Port 8083: ws://host:8083 — raw depth Z16 (for YOLO+3D, LOCAL only)
# No Python proxy needed — GStreamer handles all delivery natively.
