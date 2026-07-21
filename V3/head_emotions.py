#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent / "itog_v3_core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from v3_head import (  # noqa: E402
    HeadConfig,
    RobotHead,
    anger,
    friendliness,
    loading,
    set_led_green,
    standard_on,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Robot head emotion shortcuts.")
    parser.add_argument(
        "emotion",
        choices=(
            "friendliness",
            "anger",
            "loading",
            "standard-on",
            "green-led",
            "off",
            "clear",
        ),
    )
    parser.add_argument("--led", type=int, default=0, help="LED index for green-led, 0..17.")
    parser.add_argument("--brightness", type=int, default=120)
    parser.add_argument("--speed", type=int, default=120)
    parser.add_argument("--ws-url", default="ws://esp32-control.local:81/")
    args = parser.parse_args()

    with RobotHead(HeadConfig(ws_url=args.ws_url)) as head:
        if args.emotion == "friendliness":
            result = friendliness(head, brightness=args.brightness)
        elif args.emotion == "anger":
            result = anger(head, brightness=args.brightness)
        elif args.emotion == "loading":
            result = loading(head, brightness=args.brightness, speed_ms=args.speed)
        elif args.emotion == "standard-on":
            result = standard_on(head, brightness=args.brightness)
        elif args.emotion == "green-led":
            result = [head.brightness(args.brightness), set_led_green(head, args.led)]
        elif args.emotion == "off":
            result = [head.off()]
        else:
            result = [head.clear()]

    for line in result:
        if line:
            print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
