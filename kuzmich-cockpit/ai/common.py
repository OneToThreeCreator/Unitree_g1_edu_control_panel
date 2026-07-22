"""Shared helpers for the voice robot — extracted from v3_common."""
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
    import numpy as np
    if "bool" not in np.__dict__:
        np.bool = bool
except ImportError:
    np = None


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
