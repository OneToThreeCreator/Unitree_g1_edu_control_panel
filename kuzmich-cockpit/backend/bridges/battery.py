"""Мостик батареи: подписка на DDS-топик rt/bmsstate (BmsState_).

В dry-run режиме возвращает моковое значение 72%.
Если unitree_sdk2py не установлен — логирует warning, возвращает None.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from ..config import CONFIG

log = logging.getLogger("cockpit.battery")


class BatteryBridge:
    def __init__(self) -> None:
        self._soc: Optional[int] = None
        self._lock = threading.Lock()
        self._subscriber = None

    def start(self) -> None:
        if CONFIG.dry_run:
            log.info("BatteryBridge started (DRY-RUN, mock soc=72)")
            return
        self._connect()

    def _connect(self) -> None:
        try:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize, ChannelSubscriber
            from unitree_sdk2py.idl.unitree_hg.msg.dds_ import BmsState_
        except ImportError:
            log.warning("unitree_sdk2py not found — battery data unavailable")
            return
        except Exception as exc:
            log.warning("unitree_sdk2py import failed: %s", exc)
            return

        try:
            sub = ChannelSubscriber(CONFIG.battery_dds_topic, BmsState_)
            sub.Init(self._on_bms, 10)
            self._subscriber = sub
            log.info("BatteryBridge subscribed to %s", CONFIG.battery_dds_topic)
        except Exception as exc:
            log.warning("Battery DDS subscribe failed: %s", exc)

    def _on_bms(self, msg) -> None:
        try:
            soc = int(msg.soc)
            with self._lock:
                self._soc = soc
        except Exception:
            pass

    def soc(self) -> Optional[int]:
        if CONFIG.dry_run:
            return 72
        with self._lock:
            return self._soc
