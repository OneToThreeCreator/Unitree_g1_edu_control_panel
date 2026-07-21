"""Compatibility entry point for the V3 cup approach stack.

The implementation is split into focused modules. Keep importing from this file
for old scripts and commands; it re-exports the public API and calls the same CLI.
"""
from __future__ import annotations

import sys
from pathlib import Path

CORE_DIR = Path(__file__).resolve().parent / "itog_v3_core"
VOICE_ROBOT_DIR = Path(__file__).resolve().parent / "voice_robot"
for _d in (CORE_DIR, VOICE_ROBOT_DIR):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from v3_approach import CupApproachSystem
from v3_cli import build_arg_parser, export_engine, main, print_gpu_info
from v3_common import (
    COCO_NAMES,
    DEFAULT_DEBUG_DIR,
    DEFAULT_LOG_FILE,
    DEFAULT_MODEL_PATH,
    DEFAULT_PT_MODEL_PATH,
    SCRIPT_DIR,
    _require_cv2_numpy,
    clamp,
    cv2,
    emit_event,
    limit_rate,
    np,
    speak_text,
)
from conversation import ConversationConfig, run_conversation_gate
from conversation_llama import ensure_llama_server
from v3_face import FaceRecognitionConfig, run_face_recognition_gate
from v3_hands import (
    find_process_cmdlines,
    inspire_e2_read_angles,
    inspire_e2_write_left_angles,
    log_hand_runtime_state,
    read_arm_player_hand_mode,
    read_local_json_api,
    send_arm_action,
    stop_external_robot_processes,
)
from v3_head import (
    HeadConfig,
    RobotHead,
    anger as head_anger,
    center_green as head_center_green,
    center_off as head_center_off,
    center_red as head_center_red,
    center_white as head_center_white,
    friendliness as head_friendliness,
    led_0_green,
    led_1_green,
    led_2_green,
    led_3_green,
    led_4_green,
    led_5_green,
    led_6_green,
    led_7_green,
    led_8_green,
    led_9_green,
    led_10_green,
    led_11_green,
    led_12_green,
    led_13_green,
    led_14_green,
    led_15_green,
    led_16_green,
    led_17_green,
    loading as head_loading,
    set_led_green,
)
from v3_motion import CupApproachController, RobotVelocityConfig, UnitreeG1VelocitySender, VelocityCommand
from v3_vision import Detection3D, RealSenseColorDepthCamera, TensorRTYoloEngine, VisionConfig, YoloDepthCupDetector

__all__ = [
    "COCO_NAMES",
    "DEFAULT_DEBUG_DIR",
    "DEFAULT_LOG_FILE",
    "DEFAULT_MODEL_PATH",
    "DEFAULT_PT_MODEL_PATH",
    "SCRIPT_DIR",
    "ConversationConfig",
    "CupApproachController",
    "CupApproachSystem",
    "Detection3D",
    "FaceRecognitionConfig",
    "HeadConfig",
    "RealSenseColorDepthCamera",
    "RobotHead",
    "RobotVelocityConfig",
    "TensorRTYoloEngine",
    "UnitreeG1VelocitySender",
    "VelocityCommand",
    "VisionConfig",
    "YoloDepthCupDetector",
    "_require_cv2_numpy",
    "build_arg_parser",
    "clamp",
    "cv2",
    "emit_event",
    "export_engine",
    "find_process_cmdlines",
    "head_anger",
    "head_center_green",
    "head_center_off",
    "head_center_red",
    "head_center_white",
    "head_friendliness",
    "head_loading",
    "ensure_llama_server",
    "inspire_e2_read_angles",
    "inspire_e2_write_left_angles",
    "limit_rate",
    "led_0_green",
    "led_1_green",
    "led_2_green",
    "led_3_green",
    "led_4_green",
    "led_5_green",
    "led_6_green",
    "led_7_green",
    "led_8_green",
    "led_9_green",
    "led_10_green",
    "led_11_green",
    "led_12_green",
    "led_13_green",
    "led_14_green",
    "led_15_green",
    "led_16_green",
    "led_17_green",
    "log_hand_runtime_state",
    "main",
    "np",
    "print_gpu_info",
    "read_arm_player_hand_mode",
    "read_local_json_api",
    "run_conversation_gate",
    "run_face_recognition_gate",
    "send_arm_action",
    "set_led_green",
    "speak_text",
    "stop_external_robot_processes",
]


if __name__ == "__main__":
    main()
