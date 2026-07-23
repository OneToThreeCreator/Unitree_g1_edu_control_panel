"""Camera module — FastAPI router + initialization."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Response, WebSocket
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
        return {"state": "stopped", "backend": None, "clients": 0}
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
    frame = await _camera_manager.snapshot_jpeg() if _camera_manager.active_backend_type else None
    if frame:
        return Response(
            content=frame,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )
    return Response(status_code=503, content=b"no frame available")


@router.get("/stream.mjpg")
async def camera_stream_mjpeg():
    """MJPEG fallback — served by GStreamer pipeline."""
    if _camera_manager is None:
        return Response(status_code=503, content=b"camera module not initialized")
    # TODO: proxy from GStreamer MJPEG appsink
    return Response(status_code=501, content=b"MJPEG via GStreamer not yet implemented")


# --- WebRTC signaling ---


@router.post("/webrtc/offer")
async def webrtc_offer(data: Dict[str, Any]):
    """WebRTC SDP offer/answer exchange."""
    if _camera_manager is None:
        raise HTTPException(503, "Camera module not initialized")
    # TODO: implement GStreamer webrtcbin signaling
    return {"error": "WebRTC not yet implemented", "hint": "Use /stream.mjpg fallback"}


# --- WebSocket raw BGR (for YOLO) ---


@router.websocket("/ws/raw")
async def ws_raw(ws: WebSocket, depth: bool = Query(False)):
    """WebSocket raw BGR frames — served by GStreamer appsink."""
    # TODO: proxy from GStreamer appsink
    await ws.accept()
    await ws.send_json({"error": "Raw BGR via GStreamer not yet implemented"})
    await ws.close()
