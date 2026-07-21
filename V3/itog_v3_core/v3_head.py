"""Robot head helpers — ESP32-C3 WebSocket client.

Connects to the ESP32-C3 "Умная голова Кузьмича" via WebSocket (port 81)
and sends JSON commands for LED animations and servo eye control.

Replaces the old Arduino Nano serial interface.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

try:
    import websocket  # pip install websocket-client
except ImportError:
    websocket = None

log = logging.getLogger("head")

DEFAULT_WS_URL = "ws://esp32-control.local:81/"
DEFAULT_HEAD_PORT = "/dev/ttyUSB0"  # legacy compat


@dataclass
class HeadConfig:
    ws_url: str = DEFAULT_WS_URL
    reconnect_delay_s: float = 2.0
    command_timeout_s: float = 3.0


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{max(0, min(255, int(r))):02x}{max(0, min(255, int(g))):02x}{max(0, min(255, int(b))):02x}"


class RobotHead:
    def __init__(self, config: Optional[HeadConfig] = None) -> None:
        self.config = config or HeadConfig()
        self._ws: Optional[Any] = None

    def __enter__(self) -> "RobotHead":
        self.open()
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def open(self) -> None:
        if websocket is None:
            raise RuntimeError("websocket-client is required: pip install websocket-client")
        if self._ws is not None:
            return
        try:
            self._ws = websocket.create_connection(
                self.config.ws_url,
                timeout=self.config.command_timeout_s,
            )
            log.info("Head connected: %s", self.config.ws_url)
        except Exception as exc:
            log.warning("Head connect failed: %s", exc)
            self._ws = None

    def close(self) -> None:
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None

    def send(self, command: Dict[str, Any]) -> str:
        if self._ws is None:
            self.open()
        if self._ws is None:
            return ""
        try:
            self._ws.send(json.dumps(command))
            # Read response (state JSON from ESP32)
            self._ws.settimeout(self.config.command_timeout_s)
            resp = self._ws.recv()
            return resp
        except Exception as exc:
            log.warning("Head send failed: %s", exc)
            self.close()
            return ""

    def _send_simple(self, cmd: str, **kwargs: Any) -> str:
        payload = {"cmd": cmd}
        payload.update(kwargs)
        return self.send(payload)

    def brightness(self, value: int) -> str:
        return self._send_simple("led_brightness", value=max(0, min(255, int(value))))

    def set_color(self, hex_color: str) -> str:
        return self._send_simple("led_color", color=hex_color)

    def rgb(self, red: int, green: int, blue: int) -> str:
        return self.set_color(_rgb_to_hex(red, green, blue))

    def animation(self, name: str) -> str:
        return self._send_simple("led_animation", name=name)

    def speed(self, value: int) -> str:
        return self._send_simple("led_speed", value=max(10, min(500, int(value))))

    def off(self) -> str:
        return self.animation("off")

    def clear(self) -> str:
        return self.animation("off")

    def static_color(self, red: int, green: int, blue: int) -> str:
        self.set_color(_rgb_to_hex(red, green, blue))
        return self.animation("static")

    def loading(self, brightness: int = 120, speed_ms: int = 120) -> list[str]:
        return [
            self.brightness(brightness),
            self.speed(speed_ms),
            self.animation("rainbow"),
        ]

    def loading_color(self, red: int, green: int, blue: int, brightness: int = 120, speed_ms: int = 120) -> list[str]:
        return [
            self.brightness(brightness),
            self.speed(speed_ms),
            self.set_color(_rgb_to_hex(red, green, blue)),
            self.animation("static"),
        ]

    def anime(self, command: str, brightness: int = 120, speed_ms: int = 120) -> list[str]:
        return [
            self.brightness(brightness),
            self.speed(speed_ms),
            self.animation(command),
        ]

    # ── Servo macros ──────────────────────────────────────────────────
    def servo_macro(self, name: str) -> str:
        return self._send_simple("servo_macro", name=name)

    def servo_macro_stop(self) -> str:
        return self._send_simple("servo_macro_stop")

    def servo(self, which: int, angle: int) -> str:
        return self._send_simple("servo", which=which, angle=angle)

    def servo_both(self, angle: int) -> str:
        return self._send_simple("servo_both", angle=angle)

    def servo_speed(self, value: int) -> str:
        return self._send_simple("servo_speed", value=max(1, min(100, int(value))))


# ── Emotion helpers (backward-compatible with kuzmich_companion.py) ───

def set_led_green(head: RobotHead, index: int) -> str:
    """Set a single LED to green (mapped to static green on ESP32)."""
    return head.static_color(0, 255, 0)


def led_0_green(head: RobotHead) -> str:
    return set_led_green(head, 0)


def led_1_green(head: RobotHead) -> str:
    return set_led_green(head, 1)


def led_2_green(head: RobotHead) -> str:
    return set_led_green(head, 2)


def led_3_green(head: RobotHead) -> str:
    return set_led_green(head, 3)


def led_4_green(head: RobotHead) -> str:
    return set_led_green(head, 4)


def led_5_green(head: RobotHead) -> str:
    return set_led_green(head, 5)


def led_6_green(head: RobotHead) -> str:
    return set_led_green(head, 6)


def led_7_green(head: RobotHead) -> str:
    return set_led_green(head, 7)


def led_8_green(head: RobotHead) -> str:
    return set_led_green(head, 8)


def led_9_green(head: RobotHead) -> str:
    return set_led_green(head, 9)


def led_10_green(head: RobotHead) -> str:
    return set_led_green(head, 10)


def led_11_green(head: RobotHead) -> str:
    return set_led_green(head, 11)


def led_12_green(head: RobotHead) -> str:
    return set_led_green(head, 12)


def led_13_green(head: RobotHead) -> str:
    return set_led_green(head, 13)


def led_14_green(head: RobotHead) -> str:
    return set_led_green(head, 14)


def led_15_green(head: RobotHead) -> str:
    return set_led_green(head, 15)


def led_16_green(head: RobotHead) -> str:
    return set_led_green(head, 16)


def led_17_green(head: RobotHead) -> str:
    return set_led_green(head, 17)


def center_green(head: RobotHead) -> str:
    return head.static_color(0, 255, 0)


def center_red(head: RobotHead) -> str:
    return head.static_color(255, 0, 0)


def center_white(head: RobotHead) -> str:
    return head.static_color(255, 255, 255)


def center_off(head: RobotHead) -> str:
    return head.off()


def ring_color(head: RobotHead, red: int, green: int, blue: int, brightness: int = 120) -> list[str]:
    return [
        head.brightness(brightness),
        head.static_color(red, green, blue),
    ]


def standard_on(head: RobotHead, brightness: int = 120) -> list[str]:
    return ring_color(head, 0, 120, 255, brightness=brightness)


def friendliness(head: RobotHead, brightness: int = 120) -> list[str]:
    return ring_color(head, 0, 255, 0, brightness=brightness)


def anger(head: RobotHead, brightness: int = 120) -> list[str]:
    return ring_color(head, 255, 0, 0, brightness=brightness)


def loading(head: RobotHead, brightness: int = 120, speed_ms: int = 120) -> list[str]:
    return head.loading(brightness=brightness, speed_ms=speed_ms)


def loading_blue(head: RobotHead, brightness: int = 120, speed_ms: int = 120) -> list[str]:
    return head.loading_color(0, 0, 255, brightness=brightness, speed_ms=speed_ms)
