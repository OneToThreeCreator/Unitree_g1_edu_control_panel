"""Общее состояние пульта: режим, interlock, аварийный стоп, журнал событий.

Interlock (из инструкции по телеоперации): ручной пульт и VR-телеоперация —
взаимоисключающие. Пока активна телеоперация, ручные команды движения/рук/кисти
блокируются. Аварийный стоп (E-STOP) блокирует всю моторику до явного сброса.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Dict, List, Tuple

MODE_MANUAL = "manual"
MODE_TELEOP = "teleop"


class CockpitState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.mode: str = MODE_MANUAL
        self.estop: bool = False
        self.events: Deque[Dict] = deque(maxlen=200)

    # --- режим ---
    def set_mode(self, mode: str) -> str:
        if mode not in (MODE_MANUAL, MODE_TELEOP):
            raise ValueError(f"неизвестный режим: {mode}")
        with self._lock:
            self.mode = mode
        return mode

    def set_estop(self, engaged: bool) -> bool:
        with self._lock:
            self.estop = bool(engaged)
        return self.estop

    # --- проверки прав на действие ---
    def motion_allowed(self) -> Tuple[bool, str]:
        """Разрешено ли слать МОТОРНЫЕ команды (движение/руки/кисть)."""
        with self._lock:
            if self.estop:
                return False, "АВАРИЙНЫЙ СТОП активен — сбросьте, чтобы управлять"
            if self.mode == MODE_TELEOP:
                return False, "Активна VR-телеоперация — ручная моторика заблокирована"
            return True, ""

    # --- журнал ---
    def log_event(self, level: str, msg: str) -> Dict:
        ev = {"t": "event", "level": level, "msg": msg, "ts": time.time()}
        with self._lock:
            self.events.append(ev)
        return ev

    def recent_events(self, n: int = 30) -> List[Dict]:
        with self._lock:
            return list(self.events)[-n:]

    def snapshot(self) -> Dict:
        with self._lock:
            return {"mode": self.mode, "estop": self.estop}


STATE = CockpitState()
