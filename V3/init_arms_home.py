#!/usr/bin/env python3
"""Put G1 arms and Inspire E2 fingers into the initial/home state.

This script does not send walking commands. It only talks to:
  - g1_arm_keyframe_player over UDP for arm keyframes
  - Inspire E2 Modbus TCP endpoints for finger opening
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CORE = ROOT / "itog_v3_core"
sys.path.insert(0, str(CORE))

from v3_hands import (  # noqa: E402
    find_process_cmdlines,
    inspire_e2_read_angles,
    inspire_e2_write_left_angles,
    log_hand_runtime_state,
    send_arm_action,
)


OPEN_ANGLES = [1000, 1000, 1000, 1000, 1000, 1000]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize robot arms to home and open Inspire E2 fingers."
    )
    parser.add_argument("interface", nargs="?", default="eth0")
    parser.add_argument("--arm-port", type=int, default=15001)
    parser.add_argument("--arm-action-timeout", type=float, default=10.0)
    parser.add_argument("--arm-player", default="/home/unitree/g1_arm_keyframe_player")
    parser.add_argument("--arm-player-config", default="/home/unitree/arm_player.cfg")
    parser.add_argument("--no-start-arm-player", action="store_true")
    parser.add_argument("--skip-arm-open", action="store_true")
    parser.add_argument("--skip-arm-home", action="store_true")
    parser.add_argument("--skip-fingers", action="store_true")
    parser.add_argument("--no-left-hand", action="store_true")
    parser.add_argument("--no-right-hand", action="store_true")
    parser.add_argument("--left-hand-ip", default="192.168.123.210")
    parser.add_argument("--right-hand-ip", default="192.168.123.211")
    parser.add_argument("--hand-port", type=int, default=6000)
    parser.add_argument("--hand-timeout", type=float, default=2.0)
    parser.add_argument("--finger-angle", type=int, default=1000)
    parser.add_argument("--finger-settle-s", type=float, default=0.8)
    parser.add_argument("--log-file", default="")
    return parser.parse_args()


def configure_logging(log_file: str) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        handlers=handlers,
    )


def ensure_arm_player(args: argparse.Namespace) -> subprocess.Popen | None:
    running = find_process_cmdlines("g1_arm_keyframe_player")
    if running:
        logging.info("arm-player already running: %s", running[0])
        return None

    if args.no_start_arm_player:
        logging.warning("arm-player is not running and auto-start is disabled.")
        return None

    player = Path(args.arm_player)
    config = Path(args.arm_player_config)
    if not player.exists():
        fallback = ROOT / "g1_arm_keyframe_player"
        if fallback.exists():
            player = fallback
    if not config.exists():
        fallback = ROOT / "arm_player.cfg"
        if fallback.exists():
            config = fallback

    if not player.exists():
        raise FileNotFoundError(f"arm-player binary not found: {args.arm_player}")
    if not config.exists():
        raise FileNotFoundError(f"arm-player config not found: {args.arm_player_config}")

    log_path = ROOT / "g1_arm_keyframe_player.runtime.log"
    log_file = log_path.open("ab", buffering=0)
    cmd = [
        str(player),
        "--iface",
        args.interface,
        "--udp-port",
        str(args.arm_port),
        "--config",
        str(config),
    ]
    logging.info("starting arm-player: %s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(1.2)
    return proc


def open_hand(label: str, host: str, args: argparse.Namespace) -> bool:
    angles = [int(args.finger_angle)] * 6
    logging.info("opening %s hand host=%s:%d angles=%s", label, host, args.hand_port, angles)
    ok = inspire_e2_write_left_angles(
        angles,
        host=host,
        port=args.hand_port,
        timeout_s=args.hand_timeout,
        label=f"init_open_{label}",
        settle_s=args.finger_settle_s,
    )
    if ok:
        inspire_e2_read_angles(host, port=args.hand_port, timeout_s=args.hand_timeout)
    else:
        logging.warning("%s hand open failed", label)
    return ok


def main() -> int:
    args = parse_args()
    configure_logging(args.log_file)
    config_path = Path(args.arm_player_config)
    if not config_path.exists() and (ROOT / "arm_player.cfg").exists():
        config_path = ROOT / "arm_player.cfg"

    logging.info("initializing arms/fingers; no walking commands will be sent")
    ensure_arm_player(args)
    log_hand_runtime_state("init_before", config_path)

    ok = True

    if not args.skip_arm_open:
        logging.info("ARM action: open")
        ok = send_arm_action("open", port=args.arm_port, timeout_s=args.arm_action_timeout) and ok
        time.sleep(0.3)

    if not args.skip_fingers:
        if not args.no_left_hand:
            ok = open_hand("left", args.left_hand_ip, args) and ok
        if not args.no_right_hand:
            ok = open_hand("right", args.right_hand_ip, args) and ok

    if not args.skip_arm_home:
        logging.info("ARM action: home")
        ok = send_arm_action("home", port=args.arm_port, timeout_s=args.arm_action_timeout) and ok

    log_hand_runtime_state("init_after", config_path)
    logging.info("init result: %s", "OK" if ok else "FAILED")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
