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
_webrtc_sessions: Dict[int, JanusClient] = {}  # handle_id → client


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


# --- WebRTC signaling (Janus Streaming plugin) ---


@router.post("/webrtc/offer")
async def webrtc_offer(data: Dict[str, Any]):
    """WebRTC SDP offer/answer exchange via Janus Streaming plugin.

    Flow:
    1. Browser sends SDP offer
    2. Backend creates Janus session, attaches to Streaming plugin
    3. Sends "watch" with SDP offer to Janus
    4. Janus returns SDP answer
    5. Returns answer to browser
    """
    if _camera_manager is None or _janus_client is None:
        raise HTTPException(503, "Camera module not initialized")

    sdp = data.get("sdp")
    sdp_type = data.get("type")
    if sdp_type != "offer" or not sdp:
        raise HTTPException(400, "Expected SDP offer with type='offer'")

    try:
        # Create fresh session for each viewer
        await _janus_client.close()
        _janus_client.__init__(_camera_manager.config.janus_http_url)

        await _janus_client.create_session()
        await _janus_client.attach_plugin("janus.plugin.streaming")

        # Detect H.265 support from browser SDP offer
        has_h265 = "H265" in sdp.upper() or "HEVC" in sdp.upper()
        mountpoint_id = 1 if has_h265 else 2  # 1=H.265, 2=H.264
        log.info("WebRTC: browser %s, using mountpoint %d", "H.265" if has_h265 else "H.264", mountpoint_id)

        # Watch the mountpoint with SDP offer
        result = await _janus_client.watch(mountpoint_id=mountpoint_id, sdp=sdp)

        # Janus Streaming returns SDP offer in jsep (not answer!)
        # Flow: browser→offer → Janus→offer → browser→answer → Janus→start
        jsep = result.get("jsep", {})
        answer_sdp = jsep.get("sdp")

        if answer_sdp:
            # Store session for later answer relay
            _webrtc_sessions[_janus_client._handle_id] = _janus_client
            return {"type": "offer", "sdp": answer_sdp}

        plugindata = result.get("plugindata", {})
        data_response = plugindata.get("data", {})
        log.warning("Unexpected Janus response: status=%s", data_response.get("status"))
        raise HTTPException(502, "Janus did not return SDP")

    except Exception as e:
        log.error("WebRTC signaling failed: %s", e)
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(502, f"WebRTC signaling error: {e}")


@router.post("/webrtc/answer")
async def webrtc_answer(data: Dict[str, Any]):
    """Receive browser SDP answer and forward to Janus.

    Flow: Janus sent offer → browser created answer → now forward to Janus.
    """
    if _janus_client is None:
        raise HTTPException(503, "Camera module not initialized")

    sdp = data.get("sdp")
    sdp_type = data.get("type")
    if sdp_type != "answer" or not sdp:
        raise HTTPException(400, "Expected SDP answer with type='answer'")

    try:
        # Start the stream with the answer
        await _janus_client.start()
        log.info("WebRTC answer forwarded to Janus, stream started")
        return {"status": "ok"}
    except Exception as e:
        log.error("WebRTC answer failed: %s", e)
        raise HTTPException(502, f"WebRTC answer error: {e}")


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
