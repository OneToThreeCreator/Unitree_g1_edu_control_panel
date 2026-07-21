#!/usr/bin/env python3
"""Asynchronous Kuzmich companion mode for Unitree G1.

Records audio from the microphone and sends it directly to a multimodal LLM.
No STT, no wake-word — the model decides what was said and responds.

Configuration is loaded from base.ini (INI format). Send SIGUSR1 to the
running process to hot-reload the config without restarting.
"""
from __future__ import annotations

import argparse
import asyncio
import functools
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

VOICE_DIR = Path(__file__).resolve().parent

from common import emit_event
from config import Config
from conversation import (
    ConversationConfig,
    _check_conversation_imports,
    _load_system_prompt,
    _record_phrase_sounddevice,
)
from conversation_llama import LLMClient, ensure_llama_server, make_llm_client, reconcile_llama_server
from conversation_vllm import (
    StreamingAudioPlayer,
    VLLMRealtimeClient,
    ensure_vllm_server,
    make_realtime_client,
    reconcile_vllm_server,
    resample_48k_to_24k,
)
from head import (
    HeadConfig,
    RobotHead,
    anger as head_anger,
    friendliness as head_friendliness,
    loading_blue as head_loading_blue,
    standard_on as head_standard_on,
)

BURUNOV_TTS_URL = "http://127.0.0.1/api/audio/tts"


async def run_blocking(func: Any, /, *args: Any, **kwargs: Any) -> Any:
    loop = asyncio.get_running_loop()
    call = functools.partial(func, *args, **kwargs)
    return await loop.run_in_executor(None, call)


def urlopen_with_retry(
    request: urllib.request.Request,
    *,
    timeout_s: float,
    attempts: int = 3,
    label: str = "http",
) -> Any:
    last_exc: Optional[BaseException] = None
    effective_timeout = max(1.0, float(timeout_s))
    for attempt in range(1, attempts + 1):
        try:
            return urllib.request.urlopen(request, timeout=effective_timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            logging.warning("%s request failed, retry %d/%d: %s", label, attempt + 1, attempts, exc)
            time.sleep(0.35 * attempt)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"{label} request failed")


def speak_text_or_play_wav(
    *,
    tts_url: str,
    text: str,
    voice: Optional[str],
    hardware_volume: Optional[int],
    amplification_db: Optional[float],
    timeout_s: float,
    playback_command: str,
    robot_audio_base_url: str,
) -> bool:
    ffmpeg_filter = os.environ.get("V3_TTS_FFMPEG_FILTER", "").strip()
    needs_local_audio_processing = bool(ffmpeg_filter)
    payload: dict[str, Any] = {
        "text": text,
        "play": not needs_local_audio_processing,
    }
    if voice:
        payload["voice"] = voice
    if hardware_volume is not None:
        payload["hardware_volume"] = int(hardware_volume)
    if amplification_db is not None:
        payload["amplification_db"] = float(amplification_db)

    request = urllib.request.Request(
        tts_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    wav_path: Optional[Path] = None
    delete_wav_path = False
    amplified_wav_path: Optional[Path] = None
    filtered_wav_path: Optional[Path] = None
    try:
        logging.info(
            "TTS request: url=%s timeout=%.1f eq=%s amp=%s volume=%s",
            tts_url,
            max(1.0, float(timeout_s)),
            bool(ffmpeg_filter),
            amplification_db,
            hardware_volume,
        )
        with urlopen_with_retry(request, timeout_s=timeout_s, label="tts") as response:
            content_type = response.headers.get("Content-Type", "").lower()
            body = response.read()

        if "audio" not in content_type and not body.startswith(b"RIFF"):
            if not needs_local_audio_processing:
                return True
            response_json = json.loads(body.decode("utf-8", errors="replace"))
            track = response_json.get("track") or response_json.get("playback", {}).get("track") or {}
            wav_candidate = track.get("path") or track.get("wav_path") or track.get("file")
            track_id = track.get("id") or track.get("track_id")
            if not wav_candidate and track_id:
                library_path = Path("/home/unitree/teleop/app/web_admin/data/audio_player/library") / f"{track_id}.wav"
                if library_path.exists():
                    wav_candidate = str(library_path)
            if not wav_candidate:
                logging.warning("TTS EQ requested, but response has no WAV path: %s", response_json)
                return True
            wav_path = Path(wav_candidate)
            if not wav_path.exists():
                logging.warning("TTS EQ requested, but WAV path does not exist: %s", wav_path)
                return True
        else:
            with tempfile.NamedTemporaryFile(prefix="kuzmich_tts_", suffix=".wav", delete=False) as wav_file:
                wav_file.write(body)
                wav_path = Path(wav_file.name)
                delete_wav_path = True

        playback_wav_path = wav_path
        if amplification_db is not None and amplification_db > 0:
            amplified_wav_path = amplify_wav_with_ffmpeg(wav_path, amplification_db)
            if amplified_wav_path is not None:
                playback_wav_path = amplified_wav_path

        pulse_sink = os.environ.get("V3_TTS_PULSE_SINK", "").strip()
        if ffmpeg_filter:
            filtered_wav_path = Path(f"/tmp/kuzmich_tts_eq_{os.getpid()}.wav")
            filter_result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    str(playback_wav_path),
                    "-af",
                    ffmpeg_filter,
                    str(filtered_wav_path),
                ],
                check=False,
            )
            if filter_result.returncode == 0:
                playback_wav_path = filtered_wav_path
            else:
                logging.warning("TTS ffmpeg EQ failed with rc=%s; using original WAV", filter_result.returncode)
        if pulse_sink:
            subprocess.run(["pactl", "set-default-sink", pulse_sink], check=False)
            result = subprocess.run(["paplay", str(playback_wav_path)], check=False)
            if filtered_wav_path is not None:
                try:
                    filtered_wav_path.unlink()
                except OSError:
                    pass
            return result.returncode == 0

        if robot_audio_base_url and play_wav_via_robot_audio_service(
            wav_path=playback_wav_path,
            title=text,
            hardware_volume=hardware_volume,
            base_url=robot_audio_base_url,
            timeout_s=timeout_s,
        ):
            return True

        command = ["aplay", "-q", str(playback_wav_path)]
        result = subprocess.run(command, check=False)
        return result.returncode == 0
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        logging.warning("TTS request/playback failed: %s", exc)
        fallback_url = robot_audio_base_url.rstrip("/") + "/api/audio/tts" if robot_audio_base_url else ""
        if fallback_url and fallback_url != tts_url:
            try:
                fallback_request = urllib.request.Request(
                    fallback_url,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen_with_retry(fallback_request, timeout_s=timeout_s, label="tts-fallback") as response:
                    response.read()
                logging.info("TTS fallback used: %s", fallback_url)
                return True
            except (urllib.error.URLError, TimeoutError, OSError) as fallback_exc:
                logging.warning("TTS fallback failed: %s", fallback_exc)
        return False
    finally:
        if wav_path is not None and delete_wav_path:
            try:
                wav_path.unlink()
            except OSError:
                pass
        if amplified_wav_path is not None:
            try:
                amplified_wav_path.unlink()
            except OSError:
                pass


def amplify_wav_with_ffmpeg(wav_path: Path, amplification_db: float) -> Optional[Path]:
    with tempfile.NamedTemporaryFile(prefix="kuzmich_tts_amp_", suffix=".wav", delete=False) as out_file:
        out_path = Path(out_file.name)
    result = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(wav_path),
            "-filter:a",
            f"volume={float(amplification_db)}dB",
            str(out_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode == 0:
        return out_path
    logging.warning("ffmpeg amplification failed: %s", result.stderr.decode(errors="replace").strip())
    try:
        out_path.unlink()
    except OSError:
        pass
    return None


def play_wav_via_robot_audio_service(
    *,
    wav_path: Path,
    title: str,
    hardware_volume: Optional[int],
    base_url: str,
    timeout_s: float,
) -> bool:
    track_id: Optional[str] = None
    try:
        safe_title = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in title[:32]).strip("_")
        filename = f"kuzmich_{safe_title or 'tts'}.wav"
        boundary = f"----kuzmich{int(time.time() * 1000)}"
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
        ).encode("utf-8")
        footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
        body = header + wav_path.read_bytes() + footer

        upload_request = urllib.request.Request(
            f"{base_url.rstrip('/')}/api/audio/upload",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        with urlopen_with_retry(upload_request, timeout_s=timeout_s, label="audio-upload") as response:
            upload_data = json.loads(response.read().decode("utf-8", errors="replace"))
        track = upload_data.get("track") or {}
        track_id = str(track.get("id") or "")
        if not track_id:
            logging.warning("Robot audio upload did not return track id: %s", upload_data)
            return False

        play_payload = {
            "track_id": track_id,
            "position_seconds": 0,
            "hardware_volume": int(hardware_volume if hardware_volume is not None else 100),
        }
        play_request = urllib.request.Request(
            f"{base_url.rstrip('/')}/api/audio/play",
            data=json.dumps(play_payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen_with_retry(play_request, timeout_s=timeout_s, label="audio-play") as response:
            play_data = json.loads(response.read().decode("utf-8", errors="replace"))
        if play_data.get("error"):
            logging.warning("Robot audio playback error: %s", play_data)
            return False
        duration = float(play_data.get("duration_seconds") or track.get("duration_seconds") or 0.0)
        if duration > 0:
            time.sleep(min(duration + 0.2, timeout_s))
        time.sleep(1.0)
        return True
    except Exception as exc:
        logging.warning("Robot audio service playback failed: %s", exc)
        return False
    finally:
        if track_id:
            try:
                delete_request = urllib.request.Request(
                    f"{base_url.rstrip('/')}/api/audio/tracks/{track_id}",
                    method="DELETE",
                )
                urlopen_with_retry(delete_request, timeout_s=3.0, attempts=1, label="audio-delete").read()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Companion configuration — built from Config (INI)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CompanionConfig:
    system_prompt_path: str = str(VOICE_DIR / "kuzmich_system_prompt.txt")
    system_prompt_max_chars: int = 3000
    input_device: Optional[str] = None
    sample_rate: int = 48000
    sound_threshold: float = 0.015
    silence_after_speech_s: float = 0.7
    no_speech_timeout_s: float = 20.0
    tts_url: str = BURUNOV_TTS_URL
    tts_voice: str = ""
    tts_volume: int = 100
    tts_amplification_db: float = 0.0
    tts_timeout_s: float = 15.0
    tts_playback_command: str = "aplay -q"
    robot_audio_base_url: str = "http://127.0.0.1"
    head_ws_url: str = "ws://esp32-control.local:81/"
    head_brightness: int = 120
    head_loading_speed_ms: int = 120
    no_head: bool = False
    # Realtime API settings (shared by all backends)
    realtime_url: str = ""
    realtime_voice: str = "alloy"
    realtime_max_session_turns: int = 50


def build_companion_config(cfg: Config) -> CompanionConfig:
    """Build :class:`CompanionConfig` from the INI :class:`Config`."""
    sp = cfg.getpath("voice", "system_prompt", fallback="kuzmich_system_prompt.txt")
    if sp and not Path(sp).is_absolute():
        sp = str(VOICE_DIR / sp)
    return CompanionConfig(
        system_prompt_path=sp,
        system_prompt_max_chars=cfg.getint("voice", "system_prompt_max_chars", fallback=3000),
        input_device=cfg.get("voice", "input_device", fallback="") or None,
        sample_rate=cfg.getint("voice", "sample_rate", fallback=48000),
        sound_threshold=cfg.getfloat("voice", "sound_threshold", fallback=0.015),
        silence_after_speech_s=cfg.getfloat("voice", "silence_after_speech", fallback=0.7),
        no_speech_timeout_s=cfg.getfloat("voice", "no_speech_timeout", fallback=20.0),
        tts_url=cfg.get("tts", "url", fallback=BURUNOV_TTS_URL),
        tts_voice=cfg.get("tts", "voice", fallback=""),
        tts_volume=cfg.getint("tts", "volume", fallback=100),
        tts_amplification_db=cfg.getfloat("tts", "amplification_db", fallback=0.0),
        tts_timeout_s=cfg.getfloat("tts", "timeout", fallback=15.0),
        tts_playback_command=cfg.get("tts", "playback_command", fallback="aplay -q"),
        robot_audio_base_url=cfg.get("tts", "robot_audio_base_url", fallback="http://127.0.0.1"),
        head_ws_url=cfg.get("head", "ws_url", fallback="ws://esp32-control.local:81/"),
        head_brightness=cfg.getint("head", "brightness", fallback=120),
        head_loading_speed_ms=cfg.getint("head", "loading_speed", fallback=120),
        no_head=cfg.getboolean("head", "no_head", fallback=False),
        realtime_url=cfg.get("realtime", "realtime_url", fallback=""),
        realtime_voice=cfg.get("realtime", "voice", fallback="alloy"),
        realtime_max_session_turns=cfg.getint("realtime", "max_session_turns", fallback=50),
    )


# ---------------------------------------------------------------------------
# Companion
# ---------------------------------------------------------------------------

class KuzmichCompanion:
    def __init__(self, config: CompanionConfig, llm_client: LLMClient, raw_config: Config) -> None:
        self.config = config
        self.llm = llm_client
        self.raw_config = raw_config
        self.sd = _check_conversation_imports()
        self.system_prompt = _load_system_prompt(config.system_prompt_path, config.system_prompt_max_chars)
        if self.system_prompt:
            emit_event(f"KUZMICH: system prompt loaded from {config.system_prompt_path}")
        elif config.system_prompt_path:
            prompt_path = Path(config.system_prompt_path).expanduser()
            if not prompt_path.exists():
                logging.warning("KUZMICH: system prompt file not found: %s", prompt_path)
                emit_event(f"KUZMICH WARNING: system prompt не найден: {prompt_path}")
            else:
                logging.warning("KUZMICH: system prompt is empty: %s", prompt_path)
                emit_event(f"KUZMICH WARNING: system prompt пуст: {prompt_path}")
        self.session_lock = asyncio.Lock()
        self.head: Optional[RobotHead] = None
        self.input_device = self.resolve_input_device()
        self.realtime_client: Optional[VLLMRealtimeClient] = None
        self._session_turn_count = 0

    def resolve_input_device(self) -> Optional[str]:
        if self.config.input_device is not None:
            return self.config.input_device
        try:
            devices = self.sd.query_devices()
        except Exception as exc:
            logging.warning("Cannot query audio devices: %s", exc)
            return None

        fallback: Optional[str] = None
        for index, device in enumerate(devices):
            max_inputs = int(device.get("max_input_channels", 0))
            if max_inputs <= 0:
                continue
            name = str(device.get("name", ""))
            if fallback is None:
                fallback = str(index)
            if "usb" in name.casefold():
                emit_event(f"KUZMICH: auto input device {index}: {name}")
                return str(index)
        if fallback is not None:
            emit_event(f"KUZMICH: fallback input device {fallback}")
        return fallback

    def open(self) -> None:
        ensure_llama_server(self.raw_config)
        ensure_vllm_server(self.raw_config)
        if not self.config.no_head:
            try:
                self.head = RobotHead(HeadConfig(ws_url=self.config.head_ws_url))
                self.head.open()
                self.apply_emotion_sync("line")
            except Exception as exc:
                logging.warning("Head disabled: %s", exc)
                self.head = None
        if self.system_prompt:
            emit_event(f"KUZMICH: system prompt loaded from {self.config.system_prompt_path}")

    def close(self) -> None:
        if self.head is not None:
            self.head.close()
            self.head = None

    async def open_realtime(self) -> None:
        """Connect the Realtime API WebSocket (must be called from async context)."""
        if not self.config.realtime_url:
            return
        self.realtime_client = make_realtime_client(
            self.raw_config,
            system_prompt=self.system_prompt,
        )
        if self.realtime_client is None:
            return
        try:
            await self.realtime_client.connect()
            emit_event("KUZMICH: Realtime API connected")
        except Exception as exc:
            logging.error("Realtime API connection failed: %s", exc)
            emit_event(f"KUZMICH: Realtime API ошибка подключения: {exc}")
            self.realtime_client = None

    async def close_realtime(self) -> None:
        """Disconnect the Realtime API WebSocket."""
        if self.realtime_client:
            await self.realtime_client.disconnect()
            self.realtime_client = None

    async def run_forever(self) -> None:
        self.open()
        await self.open_realtime()
        emit_event("KUZMICH: ready — say something and I'll respond.")
        try:
            while True:
                if self.session_lock.locked():
                    await asyncio.sleep(0.1)
                    continue
                # Record audio — waits for voice activity
                audio_bytes = await self.record_audio(
                    max_wait_s=self.config.no_speech_timeout_s,
                    phrase_time_limit_s=self.config.no_speech_timeout_s,
                )
                if audio_bytes is None:
                    continue
                # Process in a task so we can keep listening next turn
                asyncio.create_task(self.session(audio_bytes))
                await asyncio.sleep(0.05)
        finally:
            await self.close_realtime()
            self.close()

    async def record_audio(self, *, max_wait_s: float, phrase_time_limit_s: float) -> Optional[bytes]:
        """Record a phrase from the microphone. Returns raw WAV bytes or None."""
        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                return await run_blocking(
                    _record_phrase_sounddevice,
                    self.sd,
                    sample_rate=self.config.sample_rate,
                    input_device=self.input_device,
                    max_wait_s=max_wait_s,
                    phrase_time_limit_s=phrase_time_limit_s,
                    sound_threshold=self.config.sound_threshold,
                    silence_after_speech_s=self.config.silence_after_speech_s,
                )
            except Exception as exc:
                logging.warning("Voice input failed attempt %d/%d: %s", attempt + 1, max_attempts, exc)
                if attempt < max_attempts - 1:
                    await asyncio.sleep(1.2)
        return None

    async def speak(self, text: str) -> bool:
        return await run_blocking(
            speak_text_or_play_wav,
            tts_url=self.config.tts_url,
            text=text,
            voice=self.config.tts_voice or None,
            hardware_volume=self.config.tts_volume,
            amplification_db=self.config.tts_amplification_db,
            timeout_s=self.config.tts_timeout_s,
            playback_command=self.config.tts_playback_command,
            robot_audio_base_url=self.config.robot_audio_base_url,
        )

    async def session(self, audio_bytes: bytes) -> None:
        async with self.session_lock:
            await self.apply_emotion("loading")

            emit_event(f"KUZMICH: audio received ({len(audio_bytes)} bytes), sending to LLM...")

            try:
                # Route to Realtime API or HTTP backend
                if self.realtime_client is not None and self.realtime_client.is_connected:
                    await self.session_realtime(audio_bytes)
                else:
                    await self._fallback_http_session(audio_bytes)
            except Exception as exc:
                logging.exception("KUZMICH session failed: %s", exc)
                # Try HTTP fallback if realtime failed
                try:
                    if self.realtime_client is not None:
                        self.realtime_client = None
                        await self._fallback_http_session(audio_bytes)
                    else:
                        await self.speak("Нейросеть сейчас не отвечает.")
                except Exception:
                    await self.speak("Нейросеть сейчас не отвечает.")
            finally:
                await self.apply_emotion("line")

    async def session_realtime(self, audio_bytes: bytes) -> None:
        """Handle a conversation turn via the Realtime API."""
        # Check session turn limit — reset server-side history if needed
        self._session_turn_count += 1
        if (
            self.config.realtime_max_session_turns > 0
            and self._session_turn_count >= self.config.realtime_max_session_turns
        ):
            await self.realtime_client.update_session(instructions=self.system_prompt)
            self._session_turn_count = 0
            emit_event("KUZMICH: Realtime session reset (turn limit)")

        # Resample 48 kHz -> 24 kHz for Realtime API
        audio_24k = resample_48k_to_24k(audio_bytes)

        # Stream audio to WebSocket and request response
        await self.realtime_client.send_audio(audio_24k)

        # Wait for model response
        text, audio_queue = await self.realtime_client.wait_response()
        emit_event(f'KUZMICH answer: "{text}"')

        # Play audio: model-generated or external TTS
        has_model_audio = self.config.realtime_voice.lower() != "none"
        if has_model_audio and audio_queue is not None:
            player = StreamingAudioPlayer(sample_rate=24000)
            await player.feed_and_play(audio_queue)
        else:
            if not text:
                text = "Я не смог сформулировать ответ."
            await self.speak(text)

    async def _fallback_http_session(self, audio_bytes: bytes) -> None:
        """Fallback: use HTTP-based LLMClient when Realtime is unavailable."""
        answer = await self.ask_llm_audio(audio_bytes)
        if not answer:
            answer = "Я не смог сформулировать ответ."
        emit_event(f'KUZMICH answer: "{answer}"')
        await self.speak(answer)

    async def ask_llm_audio(self, audio_bytes: bytes) -> str:
        voice_system_prompt = (
            f"{self.system_prompt}\n\n"
            "Режим голосового собеседника: ты получаешь аудиозапись напрямую. "
            "Определи, обращаются ли к тебе (к имени из промта), и отвечай если да. "
            "Если это шум или случайный звук — ответь коротко или промолчи. "
            "Отвечай вслух очень коротко, 1-2 предложения, максимум 20 слов. "
            "Без списков, длинных цитат и markdown."
        )
        return await run_blocking(self.llm.generate_with_audio, audio_bytes, voice_system_prompt)

    async def apply_emotion(self, emotion: str) -> None:
        await run_blocking(self.apply_emotion_sync, emotion)

    def apply_emotion_sync(self, emotion: str) -> None:
        if self.head is None:
            return
        try:
            if emotion == "friendliness":
                response = head_friendliness(self.head, brightness=self.config.head_brightness)
            elif emotion == "anger":
                response = head_anger(self.head, brightness=self.config.head_brightness)
            elif emotion == "loading":
                response = head_loading_blue(
                    self.head,
                    brightness=self.config.head_brightness,
                    speed_ms=self.config.head_loading_speed_ms,
                )
            elif emotion == "off":
                response = self.head.off()
            else:
                response = head_standard_on(self.head, brightness=self.config.head_brightness)
            emit_event(f"HEAD emotion={emotion}: {response}")
        except Exception as exc:
            logging.warning("Head emotion failed (%s): %s", emotion, exc)


# ---------------------------------------------------------------------------
# CLI & main
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Asynchronous Kuzmich companion service.")
    parser.add_argument("--config", action="append", default=None, help="INI config file(s), loaded in order (later overrides earlier).")
    parser.add_argument("--log-file", default=str(VOICE_DIR / "kuzmich_companion.log"))
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        log_path = Path(args.log_file).expanduser().resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        handlers=handlers,
        force=True,
    )

    cfg = Config()
    if not args.config:
        print("ERROR: --config is required. Example: --config base.ini", file=sys.stderr)
        sys.exit(1)
    cfg.load(*args.config)
    cfg.on_reload(lambda: reconcile_llama_server(cfg))
    cfg.on_reload(lambda: reconcile_vllm_server(cfg))
    cfg.setup_reload_signal()

    companion_config = build_companion_config(cfg)
    llm_client = make_llm_client(cfg)

    companion = KuzmichCompanion(companion_config, llm_client, cfg)
    asyncio.run(companion.run_forever())


if __name__ == "__main__":
    main()
