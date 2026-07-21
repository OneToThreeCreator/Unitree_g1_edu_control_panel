"""Voice conversation gate — records audio and sends directly to multimodal LLM."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from common import emit_event, np, speak_text

# Type alias: LLM callback — takes (audio_wav_bytes, system_prompt) and returns answer.
LLMFn = Callable[[bytes, str], str]


@dataclass(frozen=True)
class ConversationConfig:
    enabled: bool = True
    system_prompt_path: Optional[str] = None
    system_prompt_max_chars: int = 3000
    no_speech_timeout_s: float = 10.0
    sample_rate: int = 48000
    sound_threshold: float = 0.015
    silence_after_speech_s: float = 1.2
    input_device: Optional[str] = "0"


def _check_conversation_imports() -> Any:
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise RuntimeError(
            "Для функции собеседника нужен Python-пакет: sounddevice. "
            "Локально он поставлен в .venv_voice; на роботе запусти этот файл из окружения с этим пакетом."
        ) from exc
    return sd


def _record_phrase_sounddevice(
    sd: Any,
    *,
    sample_rate: int,
    input_device: Optional[str],
    max_wait_s: float,
    phrase_time_limit_s: float,
    sound_threshold: float,
    silence_after_speech_s: float,
) -> Optional[bytes]:
    if np is None:
        raise RuntimeError("Для записи голоса нужен numpy в окружении робота.")
    chunk_s = 0.20
    chunk_frames = max(1, int(sample_rate * chunk_s))
    started = False
    start_time = time.monotonic()
    speech_start_time = 0.0
    last_voice_time = 0.0
    prebuffer: list[Any] = []
    chunks: list[Any] = []
    prebuffer_limit = max(1, int(0.8 / chunk_s))

    device: Any = input_device
    if isinstance(device, str) and device.isdigit():
        device = int(device)

    with sd.InputStream(samplerate=sample_rate, channels=1, dtype="float32", device=device) as stream:
        while True:
            data, overflowed = stream.read(chunk_frames)
            if overflowed:
                logging.warning("Voice input overflowed")
            now = time.monotonic()
            rms = float(np.sqrt(np.mean(np.square(data)))) if data.size else 0.0

            if not started:
                prebuffer.append(data.copy())
                if len(prebuffer) > prebuffer_limit:
                    prebuffer.pop(0)
                if rms >= sound_threshold:
                    started = True
                    speech_start_time = now
                    last_voice_time = now
                    chunks.extend(prebuffer)
                    prebuffer.clear()
                elif now - start_time >= max_wait_s:
                    return None
                continue

            chunks.append(data.copy())
            if rms >= sound_threshold:
                last_voice_time = now
            if now - last_voice_time >= silence_after_speech_s:
                break
            if now - speech_start_time >= phrase_time_limit_s:
                break

    if not chunks:
        return None
    samples = np.concatenate(chunks, axis=0)
    samples_i16 = np.clip(samples[:, 0] * 32767.0, -32768, 32767).astype(np.int16)
    return samples_i16.tobytes()


def _load_system_prompt(path: Optional[str], max_chars: int = 3000) -> str:
    if not path:
        return ""
    prompt_path = Path(path).expanduser()
    if not prompt_path.exists():
        logging.warning("Conversation system prompt not found: %s", prompt_path)
        return ""
    text = prompt_path.read_text(encoding="utf-8").strip()
    if max_chars > 0 and len(text) > max_chars:
        logging.info("Conversation system prompt truncated: %s -> %s chars", len(text), max_chars)
        text = text[:max_chars].rstrip()
    return text


def run_conversation_gate(
    config: ConversationConfig,
    *,
    tts_url: str,
    tts_voice: Optional[str],
    tts_volume: Optional[int],
    tts_amplification_db: Optional[float],
    tts_timeout_s: float,
    llm_fn: Optional[LLMFn] = None,
) -> None:
    """Record audio from mic and send directly to a multimodal LLM.

    No STT, no wake-word — the model decides what was said and responds.
    """
    if not config.enabled:
        return

    try:
        sd = _check_conversation_imports()
    except RuntimeError as exc:
        emit_event(f"VOICE disabled: {exc}")
        return

    system_prompt = _load_system_prompt(config.system_prompt_path, config.system_prompt_max_chars)
    if system_prompt:
        emit_event(f"VOICE: system prompt loaded from {config.system_prompt_path}")
    elif config.system_prompt_path:
        prompt_path = Path(config.system_prompt_path).expanduser()
        if not prompt_path.exists():
            logging.warning("VOICE: system prompt file not found: %s", prompt_path)
            emit_event(f"VOICE WARNING: system prompt не найден: {prompt_path}")
        else:
            logging.warning("VOICE: system prompt is empty: %s", prompt_path)
            emit_event(f"VOICE WARNING: system prompt пуст: {prompt_path}")

    if llm_fn is None:
        emit_event("VOICE: LLM не настроен.")
        return

    emit_event("VOICE: слушаю... (скажите что-нибудь)")

    audio_bytes = _record_phrase_sounddevice(
        sd,
        sample_rate=config.sample_rate,
        input_device=config.input_device,
        max_wait_s=config.no_speech_timeout_s,
        phrase_time_limit_s=config.no_speech_timeout_s,
        sound_threshold=config.sound_threshold,
        silence_after_speech_s=config.silence_after_speech_s,
    )
    if audio_bytes is None:
        emit_event("VOICE: тишина, нет речи.")
        return

    emit_event("VOICE: аудио записано, отправляю в LLM...")

    try:
        answer = llm_fn(audio_bytes, system_prompt)
    except Exception as exc:
        logging.warning("LLM request failed: %s", exc)
        answer = "Не получилось получить ответ от модели."

    if not answer:
        answer = "Я не смог сформулировать ответ."
    emit_event(f'VOICE answer: "{answer}"')
    speak_text(
        tts_url=tts_url,
        text=answer,
        voice=tts_voice,
        hardware_volume=tts_volume,
        amplification_db=tts_amplification_db,
        timeout_s=tts_timeout_s,
    )
