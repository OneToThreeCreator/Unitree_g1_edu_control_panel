"""Мостик движения: прямой вызов Unitree SDK или dry-run.

В non-dry-run режиме вызывает LocoClient.SetVelocity() напрямую —
отдельный motion-receiver не нужен. В dry-run логирует команды.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional, Tuple

from ..config import CONFIG

log = logging.getLogger("cockpit.movement")


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


class MovementBridge:
    def __init__(self, on_event: Optional[Callable[[str, str], None]] = None) -> None:
        self._on_event = on_event or (lambda level, msg: None)
        self._client = None
        self._cmd: Tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._cmd_time = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def current(self) -> Tuple[float, float, float]:
        with self._lock:
            return self._cmd

    def start(self) -> None:
        if not CONFIG.dry_run:
            self._connect_sdk()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="move-udp")
        self._thread.start()
        if CONFIG.dry_run:
            log.info("MovementBridge started (DRY-RUN)")
        elif self._client is not None:
            log.info("MovementBridge started (Unitree SDK, interface=%s)", CONFIG.sdk_interface)
        else:
            log.warning("MovementBridge started (SDK unavailable, commands dropped)")

    def _connect_sdk(self) -> None:
        try:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize
            from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient

            ChannelFactoryInitialize(0, CONFIG.sdk_interface)
            self._client = LocoClient()
            self._client.SetTimeout(10.0)
            self._client.Init()
            log.info("Unitree LocoClient connected (interface=%s)", CONFIG.sdk_interface)
        except ImportError:
            log.warning("unitree_sdk2py not found — movement will be ignored")
            self._client = None
        except Exception as exc:
            log.warning("Unitree SDK init failed: %s", exc)
            self._client = None

    def set(self, vx: float, vy: float, vyaw: float) -> Tuple[float, float, float]:
        vx = _clamp(float(vx), -CONFIG.max_vx, CONFIG.max_vx)
        vy = _clamp(float(vy), -CONFIG.max_vy, CONFIG.max_vy)
        vyaw = _clamp(float(vyaw), -CONFIG.max_vyaw, CONFIG.max_vyaw)
        with self._lock:
            self._cmd = (vx, vy, vyaw)
            self._cmd_time = time.monotonic()
        return (vx, vy, vyaw)

    def stop_move(self) -> None:
        with self._lock:
            self._cmd = (0.0, 0.0, 0.0)
            self._cmd_time = time.monotonic()

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            with self._lock:
                vx, vy, vyaw = self._cmd
                age = now - self._cmd_time
            if age > CONFIG.move_ttl_s:
                vx, vy, vyaw = 0.0, 0.0, 0.0

            if CONFIG.dry_run:
                if abs(vx) + abs(vy) + abs(vyaw) > 1e-4:
                    log.debug("[DRY] move vx=%.3f vy=%.3f wz=%.3f", vx, vy, vyaw)
            elif self._client is not None:
                try:
                    if abs(vx) < 1e-4 and abs(vy) < 1e-4 and abs(vyaw) < 1e-4:
                        self._client.StopMove()
                    else:
                        self._client.SetVelocity(vx, vy, vyaw, 0.12)
                except Exception as exc:
                    log.warning("SDK SetVelocity failed: %s", exc)
            time.sleep(CONFIG.move_repeat_s)

    def shutdown(self) -> None:
        self.stop_move()
        # Send final zeros
        if not CONFIG.dry_run and self._client is not None:
            try:
                self._client.StopMove()
            except Exception:
                pass
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        log.info("MovementBridge stopped")
