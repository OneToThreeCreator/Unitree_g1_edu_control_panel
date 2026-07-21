#!/usr/bin/env python3
from __future__ import annotations

import argparse
import glob
import shlex
import sys
import time

import serial


SIMPLE_COMMANDS = {
    "red": "RED",
    "primary": "PRIMARY",
    "rainbow": "RAINBOW",
    "loading": "LOADING",
    "loading_rainbow": "LOADING_RAINBOW",
    "center_green": "CENTER_GREEN",
    "center_red": "CENTER_RED",
    "center_white": "CENTER_WHITE",
    "center_off": "CENTER_OFF",
    "half": "HALF",
    "off": "OFF",
    "clear": "CLEAR",
    "servos": "SERVOS",
    "servos30": "SERVOS_30",
    "left": "SERVO_LEFT",
    "right": "SERVO_RIGHT",
    "center": "CENTER",
    "open": "OPEN",
    "close": "CLOSE",
    "servo_off": "SERVO_OFF",
}


def find_port() -> str:
    ports = sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*"))
    if not ports:
        raise RuntimeError("No serial ports found. Connect Nano and try again.")
    return ports[0]


def clamp_byte(value: str) -> int:
    number = int(value)
    if number < 0 or number > 255:
        raise ValueError("RGB/brightness values must be 0..255")
    return number


def translate(parts: list[str]) -> str:
    if not parts:
        raise ValueError("Empty command")

    command = parts[0].lower()

    if command in SIMPLE_COMMANDS:
        if len(parts) != 1:
            raise ValueError(f"{command} does not take arguments")
        return SIMPLE_COMMANDS[command]

    if command == "raw":
        if len(parts) < 2:
            raise ValueError("raw needs a command string")
        return " ".join(parts[1:]).upper()

    if command == "brightness":
        if len(parts) != 2:
            raise ValueError("usage: brightness 0..255")
        return f"BRIGHTNESS {clamp_byte(parts[1])}"

    if command == "rgb":
        if len(parts) != 4:
            raise ValueError("usage: rgb R G B")
        r, g, b = [clamp_byte(value) for value in parts[1:4]]
        return f"RGB {r} {g} {b}"

    if command == "pixel":
        if len(parts) != 5:
            raise ValueError("usage: pixel INDEX R G B")
        index = int(parts[1])
        r, g, b = [clamp_byte(value) for value in parts[2:5]]
        return f"PIXEL {index} {r} {g} {b}"

    if command == "fill":
        if len(parts) != 6:
            raise ValueError("usage: fill START COUNT R G B")
        start = int(parts[1])
        count = int(parts[2])
        r, g, b = [clamp_byte(value) for value in parts[3:6]]
        return f"FILL {start} {count} {r} {g} {b}"

    if command == "loading_color":
        if len(parts) != 4:
            raise ValueError("usage: loading_color R G B")
        r, g, b = [clamp_byte(value) for value in parts[1:4]]
        return f"LOADING_COLOR {r} {g} {b}"

    if command == "loading_speed":
        if len(parts) != 2:
            raise ValueError("usage: loading_speed MS")
        return f"LOADING_SPEED {int(parts[1])}"

    if command == "half_color":
        if len(parts) != 4:
            raise ValueError("usage: half_color R G B")
        r, g, b = [clamp_byte(value) for value in parts[1:4]]
        return f"HALF_COLOR {r} {g} {b}"

    raise ValueError(f"Unknown command: {command}")


class NanoController:
    def __init__(self, port: str, baud: int = 115200) -> None:
        self.port = port
        self.baud = baud
        self.connection: serial.Serial | None = None

    def __enter__(self) -> "NanoController":
        self.connection = serial.Serial(self.port, self.baud, timeout=1, write_timeout=1)
        # Opening serial resets many Arduino Nano boards.
        time.sleep(2.2)
        startup = self.read_available()
        if startup:
            print(startup)
        return self

    def __exit__(self, *_args: object) -> None:
        if self.connection:
            self.connection.close()

    def read_available(self) -> str:
        assert self.connection is not None
        return self.connection.read(1024).decode(errors="replace").strip()

    def send(self, nano_command: str) -> str:
        assert self.connection is not None
        self.connection.write((nano_command + "\n").encode())
        self.connection.flush()
        time.sleep(0.25)
        return self.read_available()


def print_help() -> None:
    print(
        "Commands:\n"
        "  red | primary | rainbow | loading | loading_rainbow | half | off | clear\n"
        "  center_green | center_red | center_white | center_off\n"
        "  brightness 0..255\n"
        "  rgb R G B\n"
        "  pixel INDEX R G B\n"
        "  fill START COUNT R G B\n"
        "  loading_color R G B\n"
        "  loading_speed MS\n"
        "  half_color R G B\n"
        "  servos | servos30 | left | right | center | open | close | servo_off\n"
        "  raw ARDUINO_COMMAND\n"
        "  exit"
    )


def run_shell(controller: NanoController) -> int:
    print_help()
    while True:
        try:
            line = input("robot-head> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not line:
            continue
        if line.lower() in {"exit", "quit"}:
            return 0
        if line.lower() in {"help", "?"}:
            print_help()
            continue

        try:
            nano_command = translate(shlex.split(line))
            response = controller.send(nano_command)
        except Exception as error:
            print(f"ERR {error}")
            continue

        if response:
            print(response)


def main() -> int:
    parser = argparse.ArgumentParser(description="Python shell for robot head Nano firmware.")
    parser.add_argument("command", nargs="*", help="Command to run, or 'shell' for interactive mode.")
    parser.add_argument("--port", default=None)
    parser.add_argument("--baud", type=int, default=115200)
    args = parser.parse_args()

    try:
        port = args.port or find_port()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1

    with NanoController(port, args.baud) as controller:
        if not args.command or args.command[0].lower() == "shell":
            return run_shell(controller)

        nano_command = translate(args.command)
        response = controller.send(nano_command)
        if response:
            print(response)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
