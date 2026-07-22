"""Конфигурация единого пульта «Кузьмич».

Все адреса и порты взяты из реального кода V3 (см. отчёт и модули itog_v3_core):
  - движение:  UDP 127.0.0.1:15100  (JSON {vx,vy,wz}), v3_motion.py
  - руки:      UDP 127.0.0.1:15001  (JSON {action,seq}), v3_hands.py
  - кисть:     MODBUS TCP 192.168.123.210:6000, v3_hands.py
  - голова:    serial /dev/ttyUSB0 @115200 (Arduino Nano), v3_head.py / nano_controller.py
  - TTS:       http://127.0.0.1/api/audio/tts, speak_bluetooth.py
  - Ollama:    http://127.0.0.1:11434, kuzmich_companion.py
  - видео:     MJPEG http://127.0.0.1:8091, see_for_robot_v3.py

DRY_RUN=1 (по умолчанию) — мостики НЕ шлют реальные пакеты, а только логируют.
Это позволяет запускать пульт на dev-машине (Windows) без робота.
На борту выставить COCKPIT_DRY_RUN=0.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

# Путь к ai (voice_robot) — kuzmich-cockpit/ai
_VOICE_ROBOT_DIR = Path(__file__).resolve().parent.parent / "ai"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Config:
    # --- Режим ---
    dry_run: bool = _env_bool("COCKPIT_DRY_RUN", True)

    # --- Сервер пульта ---
    host: str = _env("COCKPIT_HOST", "0.0.0.0")
    port: int = int(_env("COCKPIT_PORT", "8080"))

    # --- Движение (Unitree SDK напрямую) ---
    sdk_interface: str = _env("SDK_INTERFACE", "eth0")
    move_repeat_s: float = 0.05          # 20 Гц, как в v3_motion
    move_ttl_s: float = 0.55             # нет свежих команд -> нули
    max_vx: float = 0.30
    max_vy: float = 0.30
    max_vyaw: float = 0.40

    # --- Руки (arm keyframe player) ---
    arm_udp_host: str = _env("ARM_UDP_HOST", "127.0.0.1")
    arm_udp_port: int = int(_env("ARM_UDP_PORT", "15001"))
    arm_timeout_s: float = 5.0

    # --- Кисть Inspire E2 (MODBUS TCP) ---
    hand_host: str = _env("HAND_HOST", "192.168.123.210")
    hand_port: int = int(_env("HAND_PORT", "6000"))

    # --- Голова (ESP32-C3 «умная голова», WebSocket, STA-режим) ---
    # ESP32 подключается к STA-сети «SMITeleop»; WS на порту 81. Fallback AP «Kuzmich» при недоступности STA.
    # На борту укажи IP/host, доступный с Jetson: ws://esp32-control.local:81/
    head_ws_url: str = _env("HEAD_WS_URL", "ws://esp32-control.local:81/")

    # --- Голос / TTS ---
    tts_url: str = _env("TTS_URL", "http://127.0.0.1/api/audio/tts")
    tts_volume: int = int(_env("TTS_VOLUME", "100"))
    tts_amplification_db: float = float(_env("TTS_AMP_DB", "15"))

    # --- ИИ (Ollama) ---
    ollama_url: str = _env("OLLAMA_URL", "http://127.0.0.1:11434")
    ai_model_local: str = _env("AI_MODEL_LOCAL", "gemma2:9b")
    ai_model_cloud: str = _env("AI_MODEL_CLOUD", "gemma4:31b-cloud")

    # --- Видео (MJPEG от see_for_robot_v3) ---
    video_mjpeg_url: str = _env("VIDEO_MJPEG_URL", "http://127.0.0.1:8091")

    # --- Файловый менеджер ---
    files_base_dir: str = _env("FILES_BASE_DIR", str(_VOICE_ROBOT_DIR.parent))

    # --- Пресеты рук (реальные action из arm_player.cfg) ---
    arm_actions: List[str] = field(default_factory=lambda: [
        "home", "reach", "open", "close", "carry", "extend",
        "both_gesture_wrist5_flip_7_ok",
    ])

    # --- Пресеты кисти Inspire E2 (6 углов 0..1000), из отчёта ---
    hand_presets: Dict[str, List[int]] = field(default_factory=lambda: {
        "open":        [1000, 1000, 1000, 1000, 1000, 1000],
        "thumb_side":  [1000, 1000, 1000, 1000, 1000, 350],
        "grasp":       [700, 650, 450, 650, 800, 350],
    })

    # --- Голова: LED-анимации (прошивка ESP32) ---
    led_animations: List[str] = field(default_factory=lambda: [
        "off", "static", "rainbow", "rainbow_cycle", "chase",
        "breathing", "theater", "wipe", "scanner", "dual",
    ])
    # --- Голова: макросы глаз (сервы), имена из прошивки ESP32 ---
    eye_macros: Dict[str, str] = field(default_factory=lambda: {
        "Открыть":       "open",
        "Закрыть":       "close",
        "Центр":         "center",
        "Моргнуть":      "blink_both",
        "Подмигнуть":    "wink",
        "Моргнуть лев.": "blink_left",
        "Моргнуть прав.":"blink_right",
    })
    # --- Системные промты ИИ (хранятся в voice_robot/prompts) ---
    prompts_dir: str = _env("PROMPTS_DIR", str(_VOICE_ROBOT_DIR / "prompts"))
    active_prompt_link: str = _env("ACTIVE_PROMPT_LINK", str(_VOICE_ROBOT_DIR / "kuzmich_system_prompt.txt"))


CONFIG = Config()
