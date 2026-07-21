"""Мостик ИИ: диалог через Ollama.

«Внутренняя ИИ» = локальная модель на борту, «внешняя ИИ» = облачная модель —
обе обслуживаются одним Ollama (127.0.0.1:11434). Тумблер в UI выбирает модель.
Повторяет вызов из kuzmich_companion: POST /api/generate {model,prompt,stream:false}.
"""
from __future__ import annotations

import logging
from typing import Tuple

import httpx

from ..config import CONFIG

log = logging.getLogger("cockpit.ai")


def model_for(source: str) -> str:
    return CONFIG.ai_model_cloud if source == "cloud" else CONFIG.ai_model_local


async def chat(text: str, source: str = "local") -> Tuple[bool, str]:
    text = (text or "").strip()
    if not text:
        return False, "пустой запрос"
    model = model_for(source)

    if CONFIG.dry_run:
        log.info("[DRY] AI(%s/%s): %s", source, model, text)
        return True, f"[dry:{model}] Кузьмич обдумал бы: «{text}» и что-нибудь ответил."

    payload = {"model": model, "prompt": text, "stream": False}
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(f"{CONFIG.ollama_url}/api/generate", json=payload)
            resp.raise_for_status()
            data = resp.json()
        return True, str(data.get("response", "")).strip()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Ollama failed: %s", exc)
        return False, str(exc)
