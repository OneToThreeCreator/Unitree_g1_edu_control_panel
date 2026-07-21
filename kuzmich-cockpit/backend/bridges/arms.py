"""Мостик рук: отправка именованной позы (action) на arm keyframe player.

Повторяет v3_hands.send_arm_action: UDP на 127.0.0.1:15001, JSON {action,seq},
ждём ACK. Имена action берутся из arm_player.cfg (см. CONFIG.arm_actions).
"""
from __future__ import annotations

import json
import logging
import socket
import time
from typing import Tuple

from ..config import CONFIG

log = logging.getLogger("cockpit.arms")


def send_arm_action(action: str) -> Tuple[bool, str]:
    """Возвращает (ok, detail). В dry-run имитирует успех."""
    if CONFIG.dry_run:
        log.info("[DRY] arm action=%s -> 15001", action)
        return True, f"[dry] action '{action}' queued"

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(CONFIG.arm_timeout_s)
    seq = int(time.time() * 1000) % 1000000
    packet = json.dumps({"action": action, "seq": seq}).encode("utf-8")
    try:
        sock.sendto(packet, (CONFIG.arm_udp_host, CONFIG.arm_udp_port))
        data, _ = sock.recvfrom(4096)
        return True, data.decode("utf-8", errors="replace")
    except (OSError, TimeoutError) as exc:
        log.warning("arm action %s failed: %s", action, exc)
        return False, str(exc)
    finally:
        sock.close()
