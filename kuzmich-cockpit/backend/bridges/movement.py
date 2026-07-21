"""Мостик движения: непрерывный UDP-поток команд ходьбы.

Повторяет логику v3_motion.UnitreeG1VelocitySender: фоновый поток шлёт последнюю
команду {vx,vy,wz} на motion-receiver ~20 Гц; если свежих команд не было дольше
TTL — шлёт нули (защита от «залипшей» скорости при обрыве связи).
"""
from __future__ import annotations

import json
import logging
import socket
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
        self._sock: Optional[socket.socket] = None
        self._cmd: Tuple[float, float, float] = (0.0, 0.0, 0.0)
        self._cmd_time = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # текущая (уже ограниченная) команда — для телеметрии
    @property
    def current(self) -> Tuple[float, float, float]:
        with self._lock:
            return self._cmd

    def start(self) -> None:
        if not CONFIG.dry_run:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="move-udp")
        self._thread.start()
        mode = "DRY-RUN" if CONFIG.dry_run else f"udp://{CONFIG.move_udp_host}:{CONFIG.move_udp_port}"
        log.info("MovementBridge started (%s)", mode)

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

            packet = json.dumps({"vx": vx, "vy": vy, "wz": vyaw}).encode("utf-8")
            if CONFIG.dry_run:
                if abs(vx) + abs(vy) + abs(vyaw) > 1e-4:
                    log.debug("[DRY] move %s", packet.decode())
            elif self._sock is not None:
                try:
                    self._sock.sendto(packet, (CONFIG.move_udp_host, CONFIG.move_udp_port))
                except OSError as exc:
                    log.warning("UDP move send failed: %s", exc)
            time.sleep(CONFIG.move_repeat_s)

    def shutdown(self) -> None:
        # финальные нули, затем закрытие
        self.stop_move()
        if not CONFIG.dry_run and self._sock is not None:
            packet = json.dumps({"vx": 0.0, "vy": 0.0, "wz": 0.0}).encode("utf-8")
            for _ in range(5):
                try:
                    self._sock.sendto(packet, (CONFIG.move_udp_host, CONFIG.move_udp_port))
                except OSError:
                    break
                time.sleep(0.02)
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        log.info("MovementBridge stopped")
