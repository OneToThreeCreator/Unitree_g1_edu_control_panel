"""Arm player, Inspire E2 hand, and external robot process helpers."""
from __future__ import annotations

import json
import logging
import os
import signal
import socket
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional, Sequence

from v3_common import clamp, emit_event

def send_arm_action(action: str, port: int = 15001, timeout_s: float = 5.0) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(timeout_s)
    seq = int(time.time() * 1000) % 1000000
    packet = json.dumps({"action": action, "seq": seq}).encode("utf-8")
    try:
        sock.sendto(packet, ("127.0.0.1", int(port)))
        data, _ = sock.recvfrom(4096)
        emit_event(f"ARM ACK: {data.decode('utf-8', errors='replace')}")
        return True
    except (OSError, TimeoutError) as exc:
        logging.warning("Arm action %s failed: %s", action, exc)
        return False
    finally:
        sock.close()


INSPIRE_E2_ANGLE_SET_ADDR = 1486
INSPIRE_E2_ANGLE_ACT_ADDR = 1546


def _inspire_e2_modbus_request(
    host: str,
    port: int,
    pdu: bytes,
    transaction_id: int,
    timeout_s: float,
) -> bytes:
    unit_id = 1
    frame = struct.pack(">HHHB", transaction_id & 0xFFFF, 0, len(pdu) + 1, unit_id) + pdu
    with socket.create_connection((host, int(port)), timeout=timeout_s) as sock:
        sock.settimeout(timeout_s)
        sock.sendall(frame)
        return sock.recv(256)


def inspire_e2_read_angles(
    host: str,
    port: int = 6000,
    timeout_s: float = 2.0,
) -> Optional[list[int]]:
    pdu = struct.pack(">BHH", 0x03, INSPIRE_E2_ANGLE_ACT_ADDR, 6)
    try:
        data = _inspire_e2_modbus_request(host, port, pdu, int(time.time() * 1000), timeout_s)
    except OSError as exc:
        emit_event(f"HAND MODBUS read failed host={host}:{port}: {exc}")
        return None

    if len(data) < 21 or data[7] != 0x03 or data[8] != 12:
        emit_event(f"HAND MODBUS bad read response: {data.hex(' ')}")
        return None

    raw = data[9:21]
    values = [struct.unpack(">h", raw[i : i + 2])[0] for i in range(0, len(raw), 2)]
    emit_event(f"HAND MODBUS ANGLE_ACT left={values}")
    return values


def inspire_e2_write_left_angles(
    angles: Sequence[int],
    host: str,
    port: int = 6000,
    timeout_s: float = 2.0,
    label: str = "",
    settle_s: float = 1.0,
) -> bool:
    if len(angles) != 6:
        raise ValueError("Inspire E2 needs exactly 6 angle values")
    clipped = [int(clamp(float(v), -1.0, 1000.0)) for v in angles]
    payload = b"".join(struct.pack(">h", value) for value in clipped)
    pdu = struct.pack(">BHHB", 0x10, INSPIRE_E2_ANGLE_SET_ADDR, 6, len(payload)) + payload

    try:
        data = _inspire_e2_modbus_request(host, port, pdu, int(time.time() * 1000), timeout_s)
    except OSError as exc:
        emit_event(f"HAND MODBUS write failed {label} host={host}:{port} angles={clipped}: {exc}")
        return False

    ok = len(data) >= 12 and data[7] == 0x10 and data[8:12] == struct.pack(">HH", INSPIRE_E2_ANGLE_SET_ADDR, 6)
    emit_event(f"HAND MODBUS write {label}: angles={clipped} ok={ok} response={data.hex(' ')}")
    if ok:
        time.sleep(max(0.0, settle_s))
        inspire_e2_read_angles(host, port=port, timeout_s=timeout_s)
    return ok


def find_process_cmdlines(pattern: str) -> list[str]:
    matches: list[str] = []
    own_pid = os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == own_pid:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
        if cmdline and pattern in cmdline:
            matches.append(f"pid={pid} {cmdline}")
    return matches


def read_arm_player_hand_mode(config_path: Path) -> str:
    try:
        for line in config_path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.split("#", 1)[0].strip()
            parts = stripped.split()
            if len(parts) >= 3 and parts[0] == "param" and parts[1] == "hand_mode":
                return parts[2]
    except OSError as exc:
        return f"unknown(config read failed: {exc})"
    return "unknown(not set)"


def read_local_json_api(path: str, timeout_s: float = 1.0) -> Optional[dict[str, Any]]:
    url = f"http://127.0.0.1{path}"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
        emit_event(f"HAND DIAG api {path} failed: {exc}")
        return None


def log_hand_runtime_state(label: str, config_path: Path) -> None:
    tty_usb = sorted(path.name for path in Path("/dev").glob("ttyUSB*"))
    brainco = find_process_cmdlines("brainco_hand_server")
    inspire_e2 = find_process_cmdlines("inspire_e2_hand_controller")
    arm_player = find_process_cmdlines("g1_arm_keyframe_player")
    hand_mode = read_arm_player_hand_mode(config_path)
    inspire_e2_status = read_local_json_api("/api/inspire-e2-hand/status")

    emit_event(
        "HAND DIAG "
        f"{label}: hand_mode={hand_mode}; "
        f"ttyUSB={','.join(tty_usb) if tty_usb else 'NONE'}; "
        f"brainco={'RUNNING' if brainco else 'NOT_RUNNING'}; "
        f"inspire_e2={'RUNNING' if inspire_e2 else 'NOT_RUNNING'}; "
        f"arm_player={'RUNNING' if arm_player else 'NOT_RUNNING'}"
    )
    if inspire_e2_status:
        endpoints = inspire_e2_status.get("endpoints") or {}
        emit_event(
            "HAND DIAG "
            f"{label}: inspire_e2_api hand={inspire_e2_status.get('hand')} "
            f"status={inspire_e2_status.get('status')} "
            f"running={inspire_e2_status.get('running')} "
            f"left={endpoints.get('left_ip')} right={endpoints.get('right_ip')} "
            f"port={endpoints.get('port')} "
            f"summary={inspire_e2_status.get('summary')}"
        )
    for item in brainco[:3]:
        emit_event(f"HAND DIAG {label}: brainco process: {item}")
    for item in inspire_e2[:3]:
        emit_event(f"HAND DIAG {label}: inspire-e2 process: {item}")
    for item in arm_player[:3]:
        emit_event(f"HAND DIAG {label}: arm-player process: {item}")

    if inspire_e2_status and inspire_e2_status.get("hand") == "inspire_e2" and hand_mode == "brainco":
        emit_event(
            f"HAND PROBLEM {label}: real hand config is inspire_e2, "
            "but arm_player.cfg hand_mode=brainco. BrainCo handpos commands will not move Inspire E2 fingers."
        )
    if inspire_e2_status and inspire_e2_status.get("hand") == "inspire_e2" and not inspire_e2:
        emit_event(f"HAND PROBLEM {label}: Inspire E2 hands are configured, but inspire_e2_hand_controller is not running.")
    if hand_mode == "brainco" and not tty_usb:
        emit_event(f"HAND PROBLEM {label}: hand_mode=brainco, but /dev/ttyUSB* is missing. Fingers cannot move.")
    if hand_mode == "brainco" and not brainco:
        emit_event(f"HAND PROBLEM {label}: hand_mode=brainco, but brainco_hand_server is not running.")
    if not arm_player:
        emit_event(f"HAND PROBLEM {label}: g1_arm_keyframe_player is not running; arm/hand UDP actions cannot execute.")


def stop_external_robot_processes() -> None:
    own_pid = os.getpid()
    patterns = (
        "g1_sdk_udp_receiver_fsm801_cpp",
        "g1_arm_keyframe_player",
    )
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == own_pid:
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        cmdline = raw.replace(b"\x00", b" ").decode("utf-8", errors="replace")
        if not cmdline:
            continue
        if any(pattern in cmdline for pattern in patterns):
            try:
                os.kill(pid, signal.SIGTERM)
                logging.info("Stopped external robot process pid=%d cmd=%s", pid, cmdline.strip())
            except OSError as exc:
                logging.warning("Cannot stop external process pid=%d: %s", pid, exc)


