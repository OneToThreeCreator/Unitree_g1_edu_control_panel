"""Camera module — FastAPI router + initialization.

WebSocket endpoints (raw BGR, depth) are served by GStreamer natively
via `websocketserver` elements on ports 8082/8083. Python handles
REST API and LiveKit JWT token generation. WebRTC publishing is done
by camera/whip_publisher.py which reads H.265 from GStreamer's
websocketsink:8084 and publishes via webrtcbin to LiveKit WHIP.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Response, WebSocket

from .config import CameraConfig
from .manager import CameraManager

log = logging.getLogger("cockpit.camera")

router = APIRouter(prefix="/api/camera", tags=["camera"])

_camera_manager: Optional[CameraManager] = None


def init_camera(config: CameraConfig, teleop_bridge: object = None) -> None:
    global _camera_manager
    _camera_manager = CameraManager(config, teleop_bridge=teleop_bridge)
    log.info("Camera module initialized (LiveKit: %s)", config.livekit_url)


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


# --- LiveKit JWT token generation ---

@router.get("/livekit-token")
async def livekit_token(identity: str = "viewer"):
    """Generate a LiveKit JWT token for browser to join the camera room."""
    if _camera_manager is None:
        raise HTTPException(503, "Camera module not initialized")

    import time
    import jwt
    import socket

    cfg = _camera_manager.config

<<<<<<< HEAD
    # Detect WSL: if hostname resolves to 127.x.x.x, use the LAN IP instead
    # so the browser on Windows can reach LiveKit in WSL
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "127.0.0.1"

    livekit_url = f"ws://{local_ip}:7880"

    now = int(time.time())
    payload = {
        "iss": cfg.livekit_api_key,
        "sub": identity,
        "iat": now,
        "exp": now + 86400,
        "video": {
            "roomJoin": True,
            "room": cfg.livekit_room,
        },
    }
    token = jwt.encode(payload, cfg.livekit_api_secret, algorithm="HS256")
    # PyJWT 1.x returns bytes on Python 3.8, decode to str
    if isinstance(token, bytes):
        token = token.decode("utf-8")

    return {
        "token": token,
        "url": livekit_url,
    }


# NOTE: WebSocket endpoints for MJPEG, raw BGR, depth are proxied through FastAPI
# (port 8080) because browser CSP blocks cross-origin WebSocket connections.
# GStreamer websocketsink runs on separate ports (8082, 8083, 8084).
# Python proxies WebSocket data from GStreamer to clients on port 8080.
=======
@router.post("/webrtc/ice")
async def webrtc_ice(data: Dict[str, Any]):
    """Buffer ICE candidate — will be sent after start."""
    global _pending_ice

    candidate = data.get("candidate")
    if not candidate:
        raise HTTPException(400, "Missing ICE candidate")

    _pending_ice.append(candidate)
    log.info("ICE candidate buffered (%d pending)", len(_pending_ice))
    return {"status": "ok"}


@router.post("/webrtc/answer")
async def webrtc_answer(data: Dict[str, Any]):
    """Receive browser SDP answer, send start, then trickle all candidates."""
    global _pending_ice
    if _janus_client is None:
        raise HTTPException(503, "No active Janus session")

    sdp = data.get("sdp")
    sdp_type = data.get("type")
    if sdp_type != "answer" or not sdp:
        raise HTTPException(400, "Expected SDP answer with type='answer'")

    # Fix SDP answer for Janus DTLS compatibility
    sdp = sdp.replace("a=setup:actpass", "a=setup:active")

    try:
        # Step 1: Start with SDP answer in JSEP
        await _janus_client.start(sdp)

        # Step 2: Wait for Janus to process SDP
        await asyncio.sleep(0.2)

        # Step 3: Send ALL buffered ICE candidates
        sent = 0
        while _pending_ice:
            candidates = list(_pending_ice)
            _pending_ice = []
            for c in candidates:
                try:
                    await _janus_client.trickle_ice(c)
                    sent += 1
                except Exception:
                    pass
            # Check for any new candidates that arrived while sending
            await asyncio.sleep(0.05)

        log.info("WebRTC started, sent %d ICE candidates", sent)
        return {"status": "ok"}
    except Exception as e:
        log.error("WebRTC answer failed: %s", e)
        raise HTTPException(502, f"WebRTC answer error: {e}")


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
>>>>>>> e8c98a7f1a351bd52cf27447bc4ef590d2391f81


# --- WebSocket proxies (port 8080) ---

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
