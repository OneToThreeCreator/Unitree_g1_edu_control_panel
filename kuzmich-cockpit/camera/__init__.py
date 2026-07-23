"""Camera module — FastAPI router + initialization.

WebSocket endpoints (raw BGR, depth) are served by GStreamer natively
via `websocketserver` elements on ports 8082/8083. Python only handles
REST API and WebRTC signaling.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse

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
    """MJPEG fallback — tries GStreamer websocketserver:8084, falls back to legacy:8091."""
    if _camera_manager is None:
        return Response(status_code=503, content=b"camera module not initialized")
    import httpx
    from typing import AsyncIterator

    # Try GStreamer first, fallback to legacy
    sources = [
        ("http://127.0.0.1:8084/stream.mjpg", "GStreamer"),
        (_camera_manager.config.video_mjpeg_url + "/stream.mjpg", "legacy"),
    ]

    async def try_proxy(url: str, label: str) -> Optional[StreamingResponse]:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                async with client.stream("GET", url) as resp:
                    if resp.status_code != 200:
                        return None
                    # Got a connection — stream it
                    async def gen() -> AsyncIterator[bytes]:
                        try:
                            async with httpx.AsyncClient(timeout=None) as c:
                                async with c.stream("GET", url) as r:
                                    async for chunk in r.aiter_bytes():
                                        yield chunk
                        except Exception as e:
                            log.warning("MJPEG %s error: %s", label, e)
                    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")
        except Exception:
            return None

    for url, label in sources:
        result = await try_proxy(url, label)
        if result:
            log.info("MJPEG proxy connected to %s", label)
            return result

    return Response(status_code=503, content=b"No MJPEG source available (GStreamer not running, legacy not found)")


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
