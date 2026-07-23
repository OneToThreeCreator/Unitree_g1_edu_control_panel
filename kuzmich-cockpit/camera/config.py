"""Camera module configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class CameraConfig:
    # --- Capture (color) ---
    color_width: int = _env_int("CAM_COLOR_WIDTH", 1280)
    color_height: int = _env_int("CAM_COLOR_HEIGHT", 720)
    color_fps: int = _env_int("CAM_COLOR_FPS", 30)

    # --- Capture (depth — RealSense дальномер) ---
    depth_width: int = _env_int("CAM_DEPTH_WIDTH", 640)
    depth_height: int = _env_int("CAM_DEPTH_HEIGHT", 480)
    depth_fps: int = _env_int("CAM_DEPTH_FPS", 30)
    depth_enabled: bool = _env_bool("CAM_DEPTH_ENABLED", True)
    min_depth_m: float = _env_float("CAM_MIN_DEPTH_M", 0.20)
    max_depth_m: float = _env_float("CAM_MAX_DEPTH_M", 4.00)
    disable_ir_emitter: bool = _env_bool("CAM_DISABLE_IR", False)

    # --- GStreamer encoding (encoder name определяет codec) ---
    # nvv4l2h265enc → H.265 (V4L2 M2M, hardware, default)
    # nvv4l2h264enc → H.264 (V4L2 M2M, hardware, fallback)
    # libx265/libx264 → software fallback
    gst_encoder: str = _env("CAM_GST_ENCODER", "nvv4l2h265enc")
    gst_bitrate: int = _env_int("CAM_GST_BITRATE", 4000)  # kbps

    # --- WebRTC ---
    webrtc_stun_url: str = _env("CAM_WEBRTC_STUN", "stun:stun.l.google.com:19302")

    # --- GStreamer WebSocket endpoints (сервятся GStreamer напрямую) ---
    ws_raw_bgr_port: int = _env_int("CAM_WS_RAW_PORT", 8082)    # Raw BGR для YOLO
    ws_depth_port: int = _env_int("CAM_WS_DEPTH_PORT", 8083)    # Raw depth Z16 для YOLO+3D

    # --- Teleop integration ---
    teleop_api_url: str = _env("TELEOP_API_URL", "http://192.168.1.102")
    teleop_ws_url: str = _env("TELEOP_WS_URL", "ws://192.168.1.102/ws/camera/preview")
    teleop_poll_interval_s: float = _env_float("TELEOP_POLL_INTERVAL", 2.0)
    teleop_codec: str = _env("TELEOP_CODEC", "h265")

    # --- Legacy MJPEG proxy ---
    video_mjpeg_url: str = _env("VIDEO_MJPEG_URL", "http://127.0.0.1:8091")
