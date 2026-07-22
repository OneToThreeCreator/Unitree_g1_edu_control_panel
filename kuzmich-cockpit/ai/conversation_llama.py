"""OpenAI-compatible LLM client and optional llama-server manager.

Works with any API that implements ``/v1/chat/completions``:
  * local llama-server (llama.cpp) with multimodal audio support
  * OpenAI API (gpt-4o-audio-preview)
  * any compatible proxy (vLLM, LiteLLM, Ollama /v1, etc.)

Server management (auto-start / health-check) is done only when the config
section ``[llama]`` has ``start_server = true``.
"""
from __future__ import annotations

import base64
import json
import logging
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from config import Config
from common import emit_event


# ---------------------------------------------------------------------------
# LLM client — thin wrapper around /v1/chat/completions
# ---------------------------------------------------------------------------

class LLMClient:
    """Call any OpenAI-compatible ``/v1/chat/completions`` endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int = 45,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    def generate(
        self,
        user_text: str,
        system_prompt: str = "",
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_text})
        return self._chat(messages, max_tokens=max_tokens, temperature=temperature)

    def generate_with_audio(
        self,
        audio_wav: bytes,
        system_prompt: str = "",
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """Send audio to the LLM via OpenAI-compatible input_audio format.

        The model processes the audio directly — no separate STT step.
        """
        audio_b64 = base64.b64encode(audio_wav).decode("ascii")
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({
            "role": "user",
            "content": [
                {"type": "input_audio", "input_audio": {"data": audio_b64, "format": "wav"}},
            ],
        })
        return self._chat(messages, max_tokens=max_tokens, temperature=temperature)

    def _chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        payload: dict[str, Any] = {
            "messages": messages,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
            "temperature": temperature if temperature is not None else self.temperature,
            "stream": False,
        }
        if self.model:
            payload["model"] = self.model

        url = self.base_url + "/chat/completions"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120.0) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))

        choices = data.get("choices", [])
        if choices:
            return str(choices[0].get("message", {}).get("content", "")).strip()
        return ""


def make_llm_client(config: Config) -> LLMClient:
    """Build an :class:`LLMClient` from the ``[ai]`` INI section."""
    base_url = config.get("ai", "base_url", fallback="http://127.0.0.1:8080/v1")
    api_key = config.get("ai", "api_key", fallback="")
    model = config.get("ai", "model", fallback="")
    temperature = config.getfloat("ai", "temperature", fallback=0.7)
    max_tokens = config.getint("ai", "max_tokens", fallback=45)
    return LLMClient(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )


# ---------------------------------------------------------------------------
# llama-server lifecycle management
# ---------------------------------------------------------------------------

# Track params of the currently running server so we can detect config changes.
_active_model_path: str = ""
_active_ctx_size: int = 0


def _is_llama_server_running() -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-f", "llama-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:
        logging.warning("Cannot check llama-server process: %s", exc)
        return False
    return result.returncode == 0


def kill_llama_server() -> None:
    """Kill any running llama-server process."""
    if not _is_llama_server_running():
        return
    try:
        subprocess.run(
            ["pkill", "-f", "llama-server"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        logging.info("LLAMA: killed llama-server process")
        emit_event("LLAMA: остановлен llama-server")
    except OSError as exc:
        logging.warning("Cannot kill llama-server: %s", exc)


def reconcile_llama_server(config: Config) -> None:
    """Start, kill, or restart llama-server to match the current config."""
    backend = config.get("ai", "backend", fallback="llama")
    if backend != "llama":
        if _is_llama_server_running():
            logging.info("LLAMA: backend is '%s', killing local server", backend)
            kill_llama_server()
        return

    # Resolve model path the same way as ensure_llama_server
    models_dir = Path(config.getpath("llama", "models_dir", fallback="~/models"))
    model_name = config.get("ai", "model", fallback="")
    new_model = str(models_dir / model_name) if model_name else ""
    new_ctx = config.getint("llama", "ctx_size", fallback=4096)

    if _is_llama_server_running() and _active_model_path:
        if new_model == _active_model_path and new_ctx == _active_ctx_size:
            return
        logging.info(
            "LLAMA: config changed (model=%s ctx=%d -> model=%s ctx=%d), restarting",
            _active_model_path, _active_ctx_size, new_model, new_ctx,
        )
        emit_event("LLAMA: параметры изменились, перезапуск сервера")
        kill_llama_server()

    ensure_llama_server(config)


def ensure_llama_server(config: Config) -> None:
    """Start ``llama-server`` if configured and not already running."""
    global _active_model_path, _active_ctx_size

    if not config.has_section("llama"):
        return
    if not config.getboolean("llama", "start_server", fallback=False):
        return
    if _is_llama_server_running():
        emit_event("LLAMA: llama-server уже запущен.")
        return

    # Resolve model path: models_dir / model (from [ai] model)
    models_dir = Path(config.getpath("llama", "models_dir", fallback="~/models"))
    model_name = config.get("ai", "model", fallback="")
    if not model_name:
        emit_event("LLAMA: [ai] model не задан, llama-server не запущен.")
        return
    model_path = models_dir / model_name
    if not model_path.exists():
        emit_event(f"LLAMA: файл модели не найден: {model_path}")
        return

    # Derive host:port from base_url (strip /v1 suffix if present)
    base_url = config.get("ai", "base_url", fallback="http://127.0.0.1:8080/v1")
    server_addr = base_url.rstrip("/").removesuffix("/v1")
    port = server_addr.rsplit(":", 1)[-1] if ":" in server_addr else "8080"
    host = server_addr.split("://")[-1].rsplit(":", 1)[0] if "://" in server_addr else "127.0.0.1"
    host = host.rstrip("/")

    ctx_size = config.getint("llama", "ctx_size", fallback=4096)
    gpu_layers = config.getint("llama", "gpu_layers", fallback=-1)
    n_threads = config.getint("llama", "n_threads", fallback=4)
    extra_str = config.get("llama", "extra_args", fallback="")
    extra_args = extra_str.split() if extra_str.strip() else []

    log_path = Path(config.getpath("llama", "server_log", fallback="/tmp/llama_server.log"))

    cmd = [
        "llama-server",
        "-m", str(model_path),
        "--host", host,
        "--port", port,
        "--ctx-size", str(ctx_size),
        "--n-gpu-layers", str(gpu_layers),
        "--n-threads", str(n_threads),
    ]
    cmd.extend(extra_args)

    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("ab")
        subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        logging.warning("Cannot start llama-server: %s", exc)
        return

    server_wait = config.getfloat("llama", "server_wait", fallback=5.0)
    emit_event(f"LLAMA: запущен llama-server, model={model_path}, log={log_path}")
    _active_model_path = str(model_path)
    _active_ctx_size = ctx_size
    time.sleep(max(0.0, server_wait))
