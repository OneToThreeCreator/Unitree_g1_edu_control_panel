#!/usr/bin/env python3
"""Extend the left arm like the cup-grasp pose and bend selected fingers.

No walking commands are sent. The script uses:
  - g1_arm_keyframe_player UDP action API for the left arm
  - Inspire E2 Modbus TCP for the left hand fingers
"""
from __future__ import annotations

import argparse
import logging
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
    send_arm_action,
)


OPEN_ANGLES = [1000, 1000, 1000, 1000, 1000, 1000]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test left arm cup extension with selected left-hand fingers bent."
    )
    parser.add_argument("interface", nargs="?", default="eth0")
    parser.add_argument("--arm-port", type=int, default=15001)
    parser.add_argument("--arm-action-timeout", type=float, default=10.0)
    parser.add_argument("--arm-player", default="/home/unitree/g1_arm_keyframe_player")
    parser.add_argument("--arm-player-config", default="/home/unitree/arm_player.cfg")
    parser.add_argument("--left-hand-ip", default="192.168.123.210")
    parser.add_argument("--left-hand-port", type=int, default=6000)
    parser.add_argument("--left-hand-timeout", type=float, default=2.0)
    parser.add_argument("--open-angle", type=int, default=1000)
    parser.add_argument(
        "--bend-fingers",
        default="1,2",
        help=(
            "1-based controller finger indexes to bend, comma-separated. "
            "Default bends controller fingers 1 and 2, which matched physical ring finger and pinky on the left hand."
        ),
    )
    parser.add_argument("--bend-angle", type=int, default=350)
    parser.add_argument("--thumb-rotation-angle", type=int, default=1000)
    parser.add_argument("--pregrasp-action", default="left_cup_pregrasp_safe_side_20260710")
    parser.add_argument("--extend-action", default="left_cup_finish_side_grasp_table76_20260710")
    parser.add_argument("--home-action", default="home")
    parser.add_argument("--hold-s", type=float, default=5.0)
    parser.add_argument("--no-home", action="store_true", help="Leave the arm extended at the end.")
    parser.add_argument("--no-start-arm-player", action="store_true")
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
        force=True,
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
    if not player.exists() and (ROOT / "g1_arm_keyframe_player").exists():
        player = ROOT / "g1_arm_keyframe_player"
    if not config.exists() and (ROOT / "arm_player.cfg").exists():
        config = ROOT / "arm_player.cfg"

    if not player.exists():
        raise FileNotFoundError(f"arm-player binary not found: {player}")
    if not config.exists():
        raise FileNotFoundError(f"arm-player config not found: {config}")

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


def write_left_hand(angles: list[int], label: str, args: argparse.Namespace, settle_s: float = 0.8) -> bool:
    logging.info("left hand %s angles=%s", label, angles)
    ok = inspire_e2_write_left_angles(
        angles,
        host=args.left_hand_ip,
        port=args.left_hand_port,
        timeout_s=args.left_hand_timeout,
        label=label,
        settle_s=settle_s,
    )
    if ok:
        inspire_e2_read_angles(
            args.left_hand_ip,
            port=args.left_hand_port,
            timeout_s=args.left_hand_timeout,
        )
    return ok


def main() -> int:
    args = parse_args()
    configure_logging(args.log_file)
    ensure_arm_player(args)

    ok = True

    open_angles = [int(args.open_angle)] * 6
    ok = write_left_hand(open_angles, "open_left_before_two_lower_fingers", args, settle_s=0.6) and ok

    logging.info("ARM action: %s", args.pregrasp_action)
    ok = send_arm_action(args.pregrasp_action, port=args.arm_port, timeout_s=args.arm_action_timeout) and ok

    logging.info("ARM action: %s", args.extend_action)
    ok = send_arm_action(args.extend_action, port=args.arm_port, timeout_s=args.arm_action_timeout) and ok

    bend_indexes: set[int] = set()
    for raw_part in str(args.bend_fingers).split(","):
        part = raw_part.strip()
        if not part:
            continue
        finger_index = int(part)
        if not 1 <= finger_index <= 5:
            raise ValueError(f"finger index must be 1..5, got {finger_index}")
        bend_indexes.add(finger_index)

    selected_angles = [int(args.open_angle)] * 6
    for finger_index in bend_indexes:
        selected_angles[finger_index - 1] = int(args.bend_angle)
    selected_angles[5] = int(args.thumb_rotation_angle)
    ok = write_left_hand(
        selected_angles,
        f"bend_left_fingers_{'_'.join(map(str, sorted(bend_indexes))) or 'none'}",
        args,
        settle_s=1.0,
    ) and ok

    hold_s = max(0.0, float(args.hold_s))
    if hold_s > 0.0:
        logging.info("holding pose %.1fs", hold_s)
        time.sleep(hold_s)

    if not args.no_home:
        ok = write_left_hand(open_angles, "open_left_after_two_lower_fingers", args, settle_s=0.6) and ok
        logging.info("ARM action: %s", args.home_action)
        ok = send_arm_action(args.home_action, port=args.arm_port, timeout_s=args.arm_action_timeout) and ok

    logging.info("result: %s", "OK" if ok else "FAILED")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
