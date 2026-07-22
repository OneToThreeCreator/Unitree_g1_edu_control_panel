"""Camera module — FastAPI router + initialization."""
from __future__ import annotations

import logging
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Response, WebSocket
from fastapi.responses import StreamingResponse

from .config import CameraConfig
from .manager import CameraManager, CameraState
from .streaming.mjpeg import MjpegStreamer
from .streaming.ws_nal import WsNalStreamer
from .streaming.ws_raw import WsRawStreamer

log = logging.getLogger("cockpit.camera")

router = APIRouter(prefix="/api/camera", tags=["camera"])

_camera_manager: Optional[CameraManager] = None
_ws_nal: Optional[WsNalStreamer] = None
_ws_raw: Optional[WsRawStreamer] = None


def init_camera(config: CameraConfig, teleop_bridge: object = None) -> None:
    global _camera_manager, _ws_nal, _ws_raw
    _camera_manager = CameraManager(config, teleop_bridge=teleop_bridge)
    _ws_nal = WsNalStreamer(_camera_manager)
    _ws_raw = WsRawStreamer(_camera_manager)
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
    if _camera_manager is None:
        return Response(status_code=503, content=b"camera module not initialized")

    client_id = f"mjpeg-{uuid4().hex[:8]}"

    async def gen():
        queue = _camera_manager.subscribe(client_id)
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
            _camera_manager.unsubscribe(client_id)

    return StreamingResponse(
        gen(), media_type="multipart/x-mixed-replace; boundary=frame"
    )


# --- WebSocket endpoints ---


@router.websocket("/ws/stream")
async def ws_stream(ws: WebSocket, codec: str = "h264"):
    """WebSocket raw NAL delivery (primary streaming protocol)."""
    if _ws_nal is None:
        await ws.close(code=1013, reason="Camera not initialized")
        return
    await _ws_nal.handle(ws, codec)


@router.websocket("/ws/raw")
async def ws_raw(ws: WebSocket, depth: bool = Query(False)):
    """WebSocket raw BGR frames (for YOLO and other local consumers)."""
    if _ws_raw is None:
        await ws.close(code=1013, reason="Camera not initialized")
        return
    await _ws_raw.handle(ws, depth)
