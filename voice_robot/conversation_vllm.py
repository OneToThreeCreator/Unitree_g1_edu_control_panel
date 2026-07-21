"""Realtime API client for VLLM / OpenAI-compatible WebSocket-based voice.

Implements the OpenAI Realtime API protocol over WebSocket:
  - Bidirectional streaming audio (PCM16 base64 at 24 kHz)
  - Model-generated speech (bypass external TTS)
  - Server-side conversation history

Works with any server implementing the Realtime API:
  * VLLM with ``--served-model-name`` and Realtime support enabled
  * OpenAI Realtime API (``wss://api.openai.com/v1/realtime``)
  * Any compatible proxy

Usage is orchestrated by ``kuzmich_companion.py``; this module provides the
WebSocket client, audio resampling helpers, and a streaming playback wrapper.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

from common import emit_event
from config import Config


# ---------------------------------------------------------------------------
# Audio format helpers
# ---------------------------------------------------------------------------

def resample_48k_to_24k(pcm16_bytes: bytes) -> bytes:
    """Decimate PCM16 audio from 48 kHz to 24 kHz (take every other sample)."""
    arr = np.frombuffer(pcm16_bytes, dtype=np.int16)
    return arr[::2].tobytes()


def resample_24k_to_48k(pcm16_bytes: bytes) -> bytes:
    """Interpolate PCM16 audio from 24 kHz to 48 kHz (repeat each sample)."""
    arr = np.frombuffer(pcm16_bytes, dtype=np.int16)
    return np.repeat(arr, 2).tobytes()


def pcm16_bytes_to_base64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def base64_to_pcm16_bytes(b64: str) -> bytes:
    return base64.b64decode(b64)


# ---------------------------------------------------------------------------
# Realtime API WebSocket client
# ---------------------------------------------------------------------------

_REALTIME_CHUNK_MS = 20  # send 20 ms chunks
_REALTIME_SAMPLE_RATE = 24000
_REALTIME_CHUNK_SAMPLES = _REALTIME_SAMPLE_RATE * _REALTIME_CHUNK_MS // 1000  # 480


class VLLMRealtimeClient:
    """WebSocket client for OpenAI-compatible Realtime API.

    The connection is persistent across conversation turns.  Audio is streamed
    as base64-encoded PCM16 chunks at 24 kHz.
    """

    def __init__(
        self,
        *,
        realtime_url: str,
        api_key: str = "",
        model: str = "",
        voice: str = "alloy",
        modalities: list[str] | str = "text,audio",
        instructions: str = "",
        temperature: float = 0.7,
        max_output_tokens: int | None = None,
    ) -> None:
        self.realtime_url = realtime_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.voice = voice
        if isinstance(modalities, str):
            self.modalities = [m.strip() for m in modalities.split(",") if m.strip()]
        else:
            self.modalities = list(modalities)
        self.instructions = instructions
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

        self._ws: Any = None  # websocket.WebSocket
        self._session_id: Optional[str] = None
        self._running = False
        self._receive_thread: Optional[threading.Thread] = None

        # Per-response state — cleared before each new response
        self._text_buffer = ""
        self._audio_queue: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        self._response_event = asyncio.Event()
        self._session_event = asyncio.Event()

        # Lock for serialising sends (thread-safe with the receive thread)
        self._send_lock = threading.Lock()

    # -- connection lifecycle ------------------------------------------------

    async def connect(self) -> None:
        """Open the WebSocket and configure the Realtime session."""
        import websocket  # defer import — may not be installed

        url = self.realtime_url
        if self.model:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}model={self.model}"

        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # websocket.create_connection is blocking — run in executor
        loop = asyncio.get_running_loop()
        self._ws = await loop.run_in_executor(
            None,
            lambda: websocket.create_connection(
                url,
                header=headers,
                timeout=30,
            ),
        )

        self._running = True
        self._session_event.clear()

        # Start background receive thread
        self._receive_thread = threading.Thread(
            target=self._receive_loop_thread,
            daemon=True,
            name="realtime-rx",
        )
        self._receive_thread.start()

        # Configure session
        await self._send_event({
            "type": "session.update",
            "session": {
                "modalities": self.modalities,
                "voice": self.voice,
                "instructions": self.instructions,
                "temperature": self.temperature,
                **(
                    {"max_response_output_tokens": self.max_output_tokens}
                    if self.max_output_tokens is not None
                    else {}
                ),
            },
        })

        # Wait for session.created (or session.updated)
        try:
            await asyncio.wait_for(self._session_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            raise RuntimeError("Realtime API: session configuration timed out")

        emit_event(f"VLLM-RT: connected to {self.realtime_url} (session={self._session_id})")

    async def disconnect(self) -> None:
        """Close the WebSocket cleanly."""
        self._running = False
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        if self._receive_thread is not None and self._receive_thread.is_alive():
            self._receive_thread.join(timeout=3.0)
            self._receive_thread = None
        # Drain queue
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        emit_event("VLLM-RT: disconnected")

    @property
    def is_connected(self) -> bool:
        return self._running and self._ws is not None

    # -- send audio input ----------------------------------------------------

    async def send_audio(self, audio_pcm16_24k: bytes) -> None:
        """Stream audio input and request a response.

        *audio_pcm16_24k* must be PCM16 mono at 24 kHz (already resampled
        by the caller if needed).  The audio is split into 20 ms chunks and
        sent via ``input_audio_buffer.append`` events, followed by a commit
        and ``response.create``.
        """
        # Clear per-response state
        self._text_buffer = ""
        self._response_event.clear()
        # Drain any leftover audio from previous turn
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        # Split into 20 ms chunks (480 samples at 24 kHz = 960 bytes PCM16)
        chunk_bytes = _REALTIME_CHUNK_SAMPLES * 2  # 2 bytes per int16 sample
        chunks = [
            audio_pcm16_24k[i : i + chunk_bytes]
            for i in range(0, len(audio_pcm16_24k), chunk_bytes)
        ]

        for chunk in chunks:
            await self._send_event({
                "type": "input_audio_buffer.append",
                "audio": pcm16_bytes_to_base64(chunk),
            })

        await self._send_event({"type": "input_audio_buffer.commit"})
        await self._send_event({"type": "response.create"})

    # -- receive events (runs in a background thread) ------------------------

    def _receive_loop_thread(self) -> None:
        """Blocking receive loop — runs in a daemon thread."""
        while self._running and self._ws is not None:
            try:
                raw = self._ws.recv()
            except Exception as exc:
                if self._running:
                    logging.warning("VLLM-RT recv error: %s", exc)
                break

            if not raw:
                continue

            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")

            if etype == "session.created":
                self._session_id = event.get("session", {}).get("id")
                self._session_event.set()

            elif etype == "session.updated":
                self._session_event.set()

            elif etype == "response.audio.delta":
                audio_b64 = event.get("delta", "")
                if audio_b64:
                    pcm = base64_to_pcm16_bytes(audio_b64)
                    try:
                        self._audio_queue.put_nowait(pcm)
                    except asyncio.QueueFull:
                        logging.warning("VLLM-RT audio queue full, dropping chunk")

            elif etype == "response.text.delta":
                self._text_buffer += event.get("delta", "")

            elif etype == "response.done":
                self._response_event.set()

            elif etype == "error":
                err = event.get("error", {})
                logging.error("VLLM-RT server error: %s", err)
                self._response_event.set()

        # Connection closed — signal end of audio
        try:
            self._audio_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        self._running = False

    # -- wait for response ---------------------------------------------------

    async def wait_response(self) -> tuple[str, asyncio.Queue[Optional[bytes]]]:
        """Block until the model finishes its response.

        Returns ``(text, audio_queue)`` where *audio_queue* contains raw PCM16
        bytes chunks (24 kHz) followed by a ``None`` sentinel.
        """
        await self._response_event.wait()
        self._response_event.clear()
        # Put sentinel so the consumer knows we're done
        try:
            self._audio_queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
        return self._text_buffer, self._audio_queue

    # -- session management --------------------------------------------------

    async def update_session(
        self,
        *,
        instructions: Optional[str] = None,
        voice: Optional[str] = None,
        modalities: Optional[list[str]] = None,
    ) -> None:
        """Send a ``session.update`` to change session parameters."""
        session_update: dict[str, Any] = {}
        if instructions is not None:
            session_update["instructions"] = instructions
            self.instructions = instructions
        if voice is not None:
            session_update["voice"] = voice
            self.voice = voice
        if modalities is not None:
            session_update["modalities"] = modalities
            self.modalities = modalities
        if not session_update:
            return

        self._session_event.clear()
        await self._send_event({"type": "session.update", "session": session_update})
        try:
            await asyncio.wait_for(self._session_event.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            logging.warning("VLLM-RT: session.update timed out")

    # -- internal helpers ----------------------------------------------------

    async def _send_event(self, event: dict[str, Any]) -> None:
        """Send a JSON event over the WebSocket (thread-safe)."""
        if self._ws is None:
            return
        data = json.dumps(event, ensure_ascii=False)
        loop = asyncio.get_running_loop()
        with self._send_lock:
            try:
                loop.run_in_executor(None, self._ws.send, data)
            except Exception as exc:
                logging.warning("VLLM-RT send error: %s", exc)


# ---------------------------------------------------------------------------
# Streaming audio player
# ---------------------------------------------------------------------------

class StreamingAudioPlayer:
    """Play PCM16 audio chunks from an ``asyncio.Queue`` via *sounddevice*.

    Audio is consumed from *audio_queue* (each item is ``bytes`` of PCM16 at
    *sample_rate* Hz, or ``None`` as end-of-stream sentinel).  A small initial
    buffer is accumulated before playback starts to avoid underruns.
    """

    def __init__(
        self,
        sample_rate: int = _REALTIME_SAMPLE_RATE,
        device: Any = None,
    ) -> None:
        self.sample_rate = sample_rate
        self.device = device
        self._stop = threading.Event()

    async def feed_and_play(self, audio_queue: asyncio.Queue[Optional[bytes]]) -> None:
        """Read chunks from *audio_queue* and stream-play them.

        Blocks (via executor) until playback finishes or ``stop()`` is called.
        """
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError(
                "Для стримингового воспроизведения нужен пакет sounddevice."
            ) from exc

        # Accumulate initial buffer (~100 ms)
        min_buffer_samples = self.sample_rate * 100 // 1000  # 2400
        min_buffer_bytes = min_buffer_samples * 2  # PCM16

        buffer = bytearray()
        done = False

        while not done:
            try:
                chunk = await asyncio.wait_for(audio_queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                logging.warning("StreamingAudioPlayer: timed out waiting for audio")
                break

            if chunk is None:
                done = True
                break

            buffer.extend(chunk)

            # Once we have enough, start playback in a thread
            if len(buffer) >= min_buffer_bytes and not self._stop.is_set():
                break

        if not buffer or self._stop.is_set():
            return

        # Convert buffer to numpy for sounddevice
        samples = np.frombuffer(bytes(buffer), dtype=np.int16)

        # Play remaining chunks in a blocking loop via executor
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._play_streaming,
            sd,
            audio_queue,
            samples,
            done,
        )

    def _play_streaming(
        self,
        sd: Any,
        audio_queue: asyncio.Queue[Optional[bytes]],
        initial_samples: np.ndarray,
        already_done: bool,
    ) -> None:
        """Blocking streaming playback — runs in a thread executor."""
        try:
            with sd.OutputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                device=self.device,
                blocksize=480,
            ) as stream:
                # Write initial buffer
                stream.write(initial_samples)
                if already_done or self._stop.is_set():
                    return

                # Stream remaining chunks
                while not self._stop.is_set():
                    try:
                        chunk = audio_queue.get(timeout=5.0)
                    except Exception:
                        break
                    if chunk is None or self._stop.is_set():
                        break
                    samples = np.frombuffer(chunk, dtype=np.int16)
                    if samples.size > 0:
                        stream.write(samples)
        except Exception as exc:
            logging.warning("StreamingAudioPlayer playback error: %s", exc)

    def stop(self) -> None:
        """Stop playback immediately."""
        self._stop.set()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_realtime_client(
    config: Config,
    system_prompt: str = "",
) -> Optional[VLLMRealtimeClient]:
    """Build a :class:`VLLMRealtimeClient` from the ``[realtime]`` INI section.

    Returns ``None`` if ``realtime_url`` is empty (Realtime API disabled).
    Falls back to ``[ai]`` for api_key / model / temperature if not set.
    """
    realtime_url = config.get("realtime", "realtime_url", fallback="")
    if not realtime_url:
        return None

    api_key = config.get("realtime", "api_key", fallback="") or config.get("ai", "api_key", fallback="")
    model = config.get("realtime", "model", fallback="") or config.get("ai", "model", fallback="")
    voice = config.get("realtime", "voice", fallback="alloy")
    modalities = "text" if voice.lower() == "none" else "text,audio"
    temperature_str = config.get("realtime", "temperature", fallback="")
    temperature = float(temperature_str) if temperature_str else config.getfloat("ai", "temperature", fallback=0.7)

    return VLLMRealtimeClient(
        realtime_url=realtime_url,
        api_key=api_key,
        model=model,
        voice=voice,
        modalities=modalities,
        instructions=system_prompt,
        temperature=temperature,
    )


# ---------------------------------------------------------------------------
# VLLM server lifecycle management (Docker-based, analogous to llama-server)
# ---------------------------------------------------------------------------

# Track params of the currently running server so we can detect config changes.
_active_vllm_model: str = ""
_active_vllm_port: str = ""
_ACTIVE_VLLM_CONTAINER: str = "kuzmich_vllm"  # fixed container name for easy management

_DEFAULT_DOCKER_IMAGE = "dustynv/vllm:r36.4-cu129-24.04"
_DEFAULT_MODELS_DIR = "/home/unitree/models"


def _is_vllm_server_running() -> bool:
    """Check whether the VLLM Docker container is running."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", _ACTIVE_VLLM_CONTAINER],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0 and result.stdout.decode().strip() == "true"
    except OSError as exc:
        logging.warning("Cannot check vllm container: %s", exc)
        return False


def kill_vllm_server() -> None:
    """Stop and remove the VLLM Docker container."""
    if not _is_vllm_server_running():
        # Also try to remove a stopped container
        try:
            subprocess.run(
                ["docker", "rm", "-f", _ACTIVE_VLLM_CONTAINER],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            pass
        return
    try:
        subprocess.run(
            ["docker", "rm", "-f", _ACTIVE_VLLM_CONTAINER],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        logging.info("VLLM: stopped and removed container %s", _ACTIVE_VLLM_CONTAINER)
        emit_event("VLLM: остановлен vllm server (Docker)")
    except OSError as exc:
        logging.warning("Cannot stop vllm container: %s", exc)


def reconcile_vllm_server(config: Config) -> None:
    """Start, kill, or restart VLLM Docker container to match the current config."""
    backend = config.get("ai", "backend", fallback="openai")

    # If backend is not vllm, kill any running server
    if backend != "vllm":
        if _is_vllm_server_running():
            logging.info("VLLM: backend is '%s', killing local server", backend)
            kill_vllm_server()
        return

    if not config.has_section("vllm"):
        return
    if not config.getboolean("vllm", "start_server", fallback=False):
        if _is_vllm_server_running():
            kill_vllm_server()
        return

    new_model = config.get("ai", "model", fallback="")
    new_port = config.get("vllm", "port", fallback="8000")

    # Check if config changed — restart if needed
    if _is_vllm_server_running() and _active_vllm_model:
        if new_model == _active_vllm_model and new_port == _active_vllm_port:
            return
        logging.info(
            "VLLM: config changed (model=%s port=%s -> model=%s port=%s), restarting",
            _active_vllm_model, _active_vllm_port, new_model, new_port,
        )
        emit_event("VLLM: параметры изменились, перезапуск сервера")
        kill_vllm_server()

    ensure_vllm_server(config)


def ensure_vllm_server(config: Config) -> None:
    """Start VLLM Docker container if configured and not already running."""
    global _active_vllm_model, _active_vllm_port

    if not config.has_section("vllm"):
        return
    if not config.getboolean("vllm", "start_server", fallback=False):
        return
    if _is_vllm_server_running():
        emit_event("VLLM: vllm server уже запущен.")
        return

    model = config.get("ai", "model", fallback="")
    if not model:
        emit_event("VLLM: model не задан, vllm server не запущен.")
        return

    # Read config parameters
    host = config.get("vllm", "host", fallback="0.0.0.0")
    port = config.get("vllm", "port", fallback="8000")
    docker_image = config.get("vllm", "docker_image", fallback=_DEFAULT_DOCKER_IMAGE)
    models_dir = config.get("vllm", "models_dir", fallback=_DEFAULT_MODELS_DIR)
    gpu_memory_utilization = config.getfloat("vllm", "gpu_memory_utilization", fallback=0.80)
    max_model_len = config.getint("vllm", "max_model_len", fallback=4096)
    max_num_seqs = config.getint("vllm", "max_num_seqs", fallback=1)
    enforce_eager = config.getboolean("vllm", "enforce_eager", fallback=True)
    served_model_name = config.get("vllm", "served_model_name", fallback="")
    tensor_parallel_size = config.getint("vllm", "tensor_parallel_size", fallback=1)
    dtype = config.get("vllm", "dtype", fallback="auto")
    trust_remote_code = config.getboolean("vllm", "trust_remote_code", fallback=False)
    extra_str = config.get("vllm", "extra_args", fallback="")
    extra_args = extra_str.split() if extra_str.strip() else []
    log_file_path = config.getpath("vllm", "server_log", fallback="/tmp/vllm_server.log")

    # Check that docker image exists locally
    try:
        check = subprocess.run(
            ["docker", "image", "inspect", docker_image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if check.returncode != 0:
            emit_event(f"VLLM: Docker image не найден: {docker_image}. Скачайте: docker pull {docker_image}")
            return
    except OSError as exc:
        emit_event(f"VLLM: Docker недоступен: {exc}")
        return

    # Build the vllm command that runs INSIDE the container
    vllm_cmd = [
        "python", "-m", "vllm.entrypoints.openai.api_server",
        "--model", f"/workspace/models/{model}",
        "--host", host,
        "--port", port,
        "--gpu-memory-utilization", str(gpu_memory_utilization),
        "--max-model-len", str(max_model_len),
        "--max-num-seqs", str(max_num_seqs),
        "--tensor-parallel-size", str(tensor_parallel_size),
        "--dtype", dtype,
    ]
    if enforce_eager:
        vllm_cmd.append("--enforce-eager")
    if served_model_name:
        vllm_cmd.extend(["--served-model-name", served_model_name])
    if trust_remote_code:
        vllm_cmd.append("--trust-remote-code")
    vllm_cmd.extend(extra_args)

    # Build the full docker run command
    docker_cmd = [
        "docker", "run",
        "--runtime", "nvidia",
        "-d",  # detached mode
        "--rm",
        "--name", _ACTIVE_VLLM_CONTAINER,
        "--network=host",
        "--ipc=host",
        "--gpus", "all",
        "--ulimit", "memlock=-1",
        "--ulimit", "stack=67108864",
        "-v", f"{models_dir}:/workspace/models",
        docker_image,
    ] + vllm_cmd

    try:
        log_path = Path(log_file_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        result = subprocess.run(
            docker_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace").strip()
            logging.error("VLLM: docker run failed: %s", stderr)
            emit_event(f"VLLM: ошибка запуска Docker: {stderr[:200]}")
            return

        container_id = result.stdout.decode().strip()[:12]
        logging.info("VLLM: container started: %s (id=%s)", _ACTIVE_VLLM_CONTAINER, container_id)
    except OSError as exc:
        logging.warning("Cannot start vllm docker: %s", exc)
        emit_event(f"VLLM: ошибка запуска Docker: {exc}")
        return

    server_wait = config.getfloat("vllm", "server_wait", fallback=15.0)
    emit_event(f"VLLM: запущен vllm server (Docker), model={model}, port={port}, log={log_file_path}")
    _active_vllm_model = model
    _active_vllm_port = port
    time.sleep(max(0.0, server_wait))
