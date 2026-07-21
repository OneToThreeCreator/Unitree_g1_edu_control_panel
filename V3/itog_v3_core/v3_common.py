"""Shared constants and small helpers for the V3 cup approach stack."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

try:
    import cv2
except ImportError:
    cv2 = None

try:
    import numpy as np
    if "bool" not in np.__dict__:
        np.bool = bool
except ImportError:
    np = None


SCRIPT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_MODEL_PATH = SCRIPT_DIR / "yolo11n_960.engine"
DEFAULT_PT_MODEL_PATH = SCRIPT_DIR / "yolo11n.pt"
DEFAULT_DEBUG_DIR = SCRIPT_DIR / "debug_frames"
DEFAULT_LOG_FILE = SCRIPT_DIR / "itog_v3_events.log"
COCO_NAMES = {
    0: "person",
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    4: "airplane",
    5: "bus",
    6: "train",
    7: "truck",
    8: "boat",
    9: "traffic light",
    10: "fire hydrant",
    11: "stop sign",
    12: "parking meter",
    13: "bench",
    14: "bird",
    15: "cat",
    16: "dog",
    17: "horse",
    18: "sheep",
    19: "cow",
    20: "elephant",
    21: "bear",
    22: "zebra",
    23: "giraffe",
    24: "backpack",
    25: "umbrella",
    26: "handbag",
    27: "tie",
    28: "suitcase",
    29: "frisbee",
    30: "skis",
    31: "snowboard",
    32: "sports ball",
    33: "kite",
    34: "baseball bat",
    35: "baseball glove",
    36: "skateboard",
    37: "surfboard",
    38: "tennis racket",
    39: "bottle",
    40: "wine glass",
    41: "cup",
    42: "fork",
    43: "knife",
    44: "spoon",
    45: "bowl",
    46: "banana",
    47: "apple",
    48: "sandwich",
    49: "orange",
    50: "broccoli",
    51: "carrot",
    52: "hot dog",
    53: "pizza",
    54: "donut",
    55: "cake",
    56: "chair",
    57: "couch",
    58: "potted plant",
    59: "bed",
    60: "dining table",
    61: "toilet",
    62: "tv",
    63: "laptop",
    64: "mouse",
    65: "remote",
    66: "keyboard",
    67: "cell phone",
    68: "microwave",
    69: "oven",
    70: "toaster",
    71: "sink",
    72: "refrigerator",
    73: "book",
    74: "clock",
    75: "vase",
    76: "scissors",
    77: "teddy bear",
    78: "hair drier",
    79: "toothbrush",
}


def _require_cv2_numpy() -> None:
    if cv2 is None or np is None:
        raise RuntimeError("Нужны opencv-python и numpy в окружении робота.")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def limit_rate(target: float, previous: float, max_delta: float) -> float:
    return previous + clamp(target - previous, -max_delta, max_delta)


def emit_event(message: str, verbose: bool = True) -> None:
    if verbose:
        print(message)
    logging.info(message)


def speak_text(
    tts_url: str,
    text: str,
    voice: Optional[str] = None,
    hardware_volume: Optional[int] = None,
    amplification_db: Optional[float] = None,
    timeout_s: float = 3.0,
) -> bool:
    pulse_sink = os.environ.get("V3_TTS_PULSE_SINK", "").strip()
    payload: dict[str, Any] = {
        "text": text,
        "play": not bool(pulse_sink),
    }
    if voice:
        payload["voice"] = voice
    if hardware_volume is not None:
        payload["hardware_volume"] = int(hardware_volume)
    if amplification_db is not None:
        payload["amplification_db"] = float(amplification_db)

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        tts_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            response_body = response.read()
        if pulse_sink:
            response_json = json.loads(response_body.decode("utf-8", "replace"))
            track = response_json.get("track") or {}
            track_id = track.get("id") or track.get("track_id")
            wav_path = track.get("path") or track.get("wav_path") or track.get("file")
            if not wav_path and track_id:
                candidate = Path("/home/unitree/teleop/app/web_admin/data/audio_player/library") / f"{track_id}.wav"
                if candidate.exists():
                    wav_path = str(candidate)
            if not wav_path:
                raise RuntimeError(f"TTS response has no playable WAV path: {response_json!r}")
            env = os.environ.copy()
            env.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
            subprocess.run(["pactl", "set-default-sink", pulse_sink], check=False, env=env)
            playback_path = str(wav_path)
            ffmpeg_filter = os.environ.get("V3_TTS_FFMPEG_FILTER", "").strip()
            filtered_path = ""
            if ffmpeg_filter:
                filtered_path = f"/tmp/v3_tts_eq_{os.getpid()}.wav"
                filter_result = subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-loglevel",
                        "error",
                        "-i",
                        str(wav_path),
                        "-af",
                        ffmpeg_filter,
                        filtered_path,
                    ],
                    check=False,
                    env=env,
                )
                if filter_result.returncode == 0:
                    playback_path = filtered_path
                else:
                    logging.warning("TTS ffmpeg EQ failed with rc=%s; using original WAV", filter_result.returncode)
            result = subprocess.run(["paplay", playback_path], check=False, env=env)
            if filtered_path:
                try:
                    Path(filtered_path).unlink()
                except OSError:
                    pass
            if result.returncode != 0:
                raise RuntimeError(f"paplay failed with rc={result.returncode}")
        return True
    except (urllib.error.URLError, TimeoutError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        logging.warning("TTS request failed: %s", exc)
        return False
