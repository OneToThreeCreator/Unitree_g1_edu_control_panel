"""Мостик голоса: TTS робота (робот говорит вслух).

Повторяет вызов из speak_bluetooth/kuzmich_companion: POST на локальный аудио-API
робота /api/audio/tts. Здесь — тонкий асинхронный прокси на httpx.
"""
from __future__ import annotations

import logging
from typing import Tuple

import httpx

from ..config import CONFIG

log = logging.getLogger("cockpit.voice")


async def speak(text: str) -> Tuple[bool, str]:
    text = (text or "").strip()
    if not text:
        return False, "пустой текст"

    if CONFIG.dry_run:
        log.info("[DRY] TTS: %s", text)
        return True, f"[dry] сказал бы: «{text}»"

    payload = {
        "text": text,
        "play": True,
        "hardware_volume": int(CONFIG.tts_volume),
        "amplification_db": float(CONFIG.tts_amplification_db),
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(CONFIG.tts_url, json=payload)
            resp.raise_for_status()
        return True, "ok"
    except httpx.HTTPError as exc:
        log.warning("TTS failed: %s", exc)
        return False, str(exc)
