"""Мостик головы: «умная голова» Кузьмича на ESP32-C3 по WebSocket.

Голова (14× WS2812B + 2 сервы глаз) переехала с Arduino Nano на ESP32-C3 с
собственным WS-сервером (порт 81). Пульт держит постоянное WS-соединение к ней
и шлёт JSON-команды {cmd:...}. Протокол — из прошивки esp32_control_light.cpp:

  LED:    led_animation{name}, led_color{color}, led_color2{color},
          led_brightness{value 0..255}, led_speed{value 10..500}
  Глаза:  servo_macro{name}, servo{which 1|2, angle 0..180}, servo_both{angle},
          servo_speed{value 1..100}, servo_macro_stop, auto_blink{enabled,interval},
          blink_now, servo_enable{which, enabled}

В dry-run соединение не открывается — команды логируются.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional, Tuple

from ..config import CONFIG

log = logging.getLogger("cockpit.head")

try:
    import websockets  # входит в uvicorn[standard]
except ImportError:
    websockets = None


class HeadBridge:
    """Асинхронный WS-клиент к ESP32-голове с ленивым переподключением."""

    def __init__(self) -> None:
        self._ws: Optional[Any] = None
        self._lock = asyncio.Lock()

    async def _ensure(self) -> None:
        if self._ws is not None:
            return
        if websockets is None:
            raise RuntimeError("библиотека websockets недоступна")
        self._ws = await asyncio.wait_for(websockets.connect(CONFIG.head_ws_url), timeout=4.0)
        log.info("HeadBridge connected: %s", CONFIG.head_ws_url)

    async def send(self, cmd: Dict[str, Any]) -> Tuple[bool, str]:
        if not cmd.get("cmd"):
            return False, "пустая команда"

        if CONFIG.dry_run:
            log.info("[DRY] head <- %s", cmd)
            return True, f"[dry] {cmd.get('cmd')} {cmd.get('name', cmd.get('value',''))}"

        payload = json.dumps(cmd)
        async with self._lock:
            for attempt in (1, 2):  # одна попытка переподключения
                try:
                    await self._ensure()
                    await self._ws.send(payload)  # type: ignore[union-attr]
                    return True, "ok"
                except Exception as exc:  # noqa: BLE001
                    log.warning("head send failed (try %d): %s", attempt, exc)
                    await self._reset()
            return False, "ESP32-голова недоступна"

    async def _reset(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None

    def shutdown(self) -> None:
        # синхронная заглушка для on_event shutdown; закрытие сделает GC/loop
        self._ws = None
