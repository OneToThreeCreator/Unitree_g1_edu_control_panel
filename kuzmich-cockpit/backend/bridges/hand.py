"""Мостик кисти Inspire E2 (MODBUS TCP).

Повторяет v3_hands.inspire_e2_write_left_angles: пишет 6 углов (0..1000) в
регистр ANGLE_SET (1486) по MODBUS TCP на 192.168.123.210:6000.
"""
from __future__ import annotations

import logging
import socket
import struct
import time
from typing import List, Sequence, Tuple

from ..config import CONFIG

log = logging.getLogger("cockpit.hand")

INSPIRE_E2_ANGLE_SET_ADDR = 1486


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _modbus_request(pdu: bytes, transaction_id: int, timeout_s: float) -> bytes:
    unit_id = 1
    frame = struct.pack(">HHHB", transaction_id & 0xFFFF, 0, len(pdu) + 1, unit_id) + pdu
    with socket.create_connection((CONFIG.hand_host, CONFIG.hand_port), timeout=timeout_s) as sock:
        sock.settimeout(timeout_s)
        sock.sendall(frame)
        return sock.recv(256)


def write_left_angles(angles: Sequence[int], timeout_s: float = 2.0) -> Tuple[bool, str]:
    if len(angles) != 6:
        return False, "Inspire E2 требует ровно 6 значений"
    clipped: List[int] = [int(_clamp(float(v), -1.0, 1000.0)) for v in angles]

    if CONFIG.dry_run:
        log.info("[DRY] hand angles=%s -> %s:%s", clipped, CONFIG.hand_host, CONFIG.hand_port)
        return True, f"[dry] angles {clipped}"

    payload = b"".join(struct.pack(">h", value) for value in clipped)
    pdu = struct.pack(">BHHB", 0x10, INSPIRE_E2_ANGLE_SET_ADDR, 6, len(payload)) + payload
    try:
        data = _modbus_request(pdu, int(time.time() * 1000), timeout_s)
    except OSError as exc:
        log.warning("hand write failed: %s", exc)
        return False, str(exc)

    ok = (
        len(data) >= 12
        and data[7] == 0x10
        and data[8:12] == struct.pack(">HH", INSPIRE_E2_ANGLE_SET_ADDR, 6)
    )
    return ok, data.hex(" ")
