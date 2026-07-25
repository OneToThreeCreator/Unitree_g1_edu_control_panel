"""Camera module — FastAPI router + initialization.

WebSocket endpoints (raw BGR, depth) are served by GStreamer natively
via `websocketserver` elements on ports 8082/8083. Python only handles
REST API and WebRTC signaling via Janus SFU.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Response, WebSocket

from .config import CameraConfig
from .manager import CameraManager
from .janus_client import JanusClient

log = logging.getLogger("cockpit.camera")

router = APIRouter(prefix="/api/camera", tags=["camera"])

_camera_manager: Optional[CameraManager] = None
_janus_client: Optional[JanusClient] = None


def init_camera(config: CameraConfig, teleop_bridge: object = None) -> None:
    global _camera_manager, _janus_client
    _camera_manager = CameraManager(config, teleop_bridge=teleop_bridge)
    _janus_client = JanusClient(config.janus_http_url)
    log.info("Camera module initialized (Janus: %s)", config.janus_http_url)


def get_camera_manager() -> Optional[CameraManager]:
    return _camera_manager


# --- REST endpoints ---


@router.get("/status")
async def camera_status():
    if _camera_manager is None:
        return {"state": "stopped", "backend": None, "dry_run": False}
    st = _camera_manager.status()
    st["dry_run"] = _camera_manager.config.dry_run
    return st


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
    await _camera_manager.pause()
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


# --- WebRTC signaling (Janus SFU) ---


@router.post("/webrtc/offer")
async def webrtc_offer(data: Dict[str, Any]):
    """WebRTC SDP offer/answer exchange via Janus VideoRoom.

    Browser sends SDP offer, we forward to Janus which handles
    ICE/DTLS and returns SDP answer.
    """
    if _camera_manager is None or _janus_client is None:
        raise HTTPException(503, "Camera module not initialized")

    sdp = data.get("sdp")
    sdp_type = data.get("type")
    if sdp_type != "offer" or not sdp:
        raise HTTPException(400, "Expected SDP offer with type='offer'")

    try:
        # Ensure Janus session exists
        if _janus_client.session_id is None:
            await _janus_client.create_session()
            await _janus_client.attach_plugin()

        # Join room as publisher if not already
        room_id = _camera_manager.config.janus_room_id
        await _janus_client.join_room(room_id, publisher=True)
        await _janus_client.configure_publisher(video=True)

        # Publish the SDP offer to Janus
        result = await _janus_client.publish(sdp, room_id)

        # Extract SDP answer from Janus response
        plugindata = result.get("plugindata", {})
        data_response = plugindata.get("data", {})
        answer_sdp = data_response.get("sdp")

        if not answer_sdp:
            log.warning("Janus returned no SDP answer: %s", result)
            raise HTTPException(502, "Janus did not return SDP answer")

        return {"type": "answer", "sdp": answer_sdp}

    except Exception as e:
        log.error("WebRTC signaling failed: %s", e)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(502, f"WebRTC signaling error: {e}")


@router.post("/webrtc/ice")
async def webrtc_ice(data: Dict[str, Any]):
    """Forward ICE candidate to Janus."""
    if _janus_client is None:
        raise HTTPException(503, "Camera module not initialized")

    candidate = data.get("candidate")
    if not candidate:
        raise HTTPException(400, "Missing ICE candidate")

    try:
        await _janus_client.trickle_ice(candidate)
        return {"status": "ok"}
    except Exception as e:
        log.warning("ICE trickle failed: %s", e)
        return {"status": "error", "detail": str(e)}


@router.post("/webrtc/hangup")
async def webrtc_hangup():
    """Cleanup WebRTC session."""
    global _janus_client
    if _camera_manager is None:
        raise HTTPException(503, "Camera module not initialized")

    # Reset Janus client — next offer will create new session
    if _janus_client:
        await _janus_client.close()
        _janus_client = JanusClient(_camera_manager.config.janus_http_url)
    return {"status": "ok"}


# --- WebSocket proxies (port 8080) ---
# Browser CSP blocks cross-origin WebSocket to GStreamer ports (8082-8084).
# Python proxies WebSocket data from GStreamer websocketsink to browser clients.

@router.websocket("/ws/mjpeg")
async def ws_mjpeg_proxy(ws: WebSocket):
    """Proxy MJPEG from GStreamer websocketsink to browser."""
    await ws.accept()
    gst_port = _camera_manager.config.ws_raw_bgr_port
    log.info("MJPEG proxy: connecting to GStreamer ws://127.0.0.1:%s", gst_port)
    try:
        import websockets
        async with websockets.connect(f"ws://127.0.0.1:{gst_port}") as gst_ws:
            log.info("MJPEG proxy: connected to GStreamer")
            async for msg in gst_ws:
                if isinstance(msg, bytes):
                    await ws.send_bytes(msg)
    except Exception as e:
        if "1005" not in str(e):
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
        if "1005" not in str(e):
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
        if "1005" not in str(e):
            log.warning("Depth proxy error: %s", e)
        try:
            await ws.close()
        except Exception:
            pass
