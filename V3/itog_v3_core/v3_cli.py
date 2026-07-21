"""Command-line entry point for the V3 cup approach stack."""
from __future__ import annotations

import argparse
import logging
import math
import os
import subprocess
import sys
import time
from pathlib import Path

from v3_approach import CupApproachSystem
from v3_common import DEFAULT_DEBUG_DIR, DEFAULT_LOG_FILE, DEFAULT_MODEL_PATH, DEFAULT_PT_MODEL_PATH, emit_event, speak_text
from v3_face import FaceRecognitionConfig, OlegDepthFaceDetector, run_face_recognition_gate

# voice_robot modules live in a separate directory
VOICE_ROBOT_DIR = Path(__file__).resolve().parents[1] / "voice_robot"
if str(VOICE_ROBOT_DIR) not in sys.path:
    sys.path.insert(0, str(VOICE_ROBOT_DIR))
from config import Config
from conversation import ConversationConfig, run_conversation_gate
from conversation_llama import ensure_llama_server, make_llm_client, reconcile_llama_server
from v3_hands import (
    inspire_e2_write_left_angles,
    log_hand_runtime_state,
    send_arm_action,
    stop_external_robot_processes,
)
from v3_head import (
    DEFAULT_HEAD_PORT,
    HeadConfig,
    RobotHead,
    anger as head_anger,
    friendliness as head_friendliness,
    loading_blue as head_loading_blue,
)
from v3_motion import CupApproachController, RobotVelocityConfig, UnitreeG1VelocitySender
from v3_vision import VisionConfig, YoloDepthCupDetector

def print_gpu_info() -> None:
    try:
        import torch
    except ImportError:
        print("torch: not installed")
        return

    print(f"torch: {torch.__version__}")
    print(f"cuda available: {torch.cuda.is_available()}")
    print(f"cuda version: {torch.version.cuda}")
    if torch.cuda.is_available():
        print(f"device count: {torch.cuda.device_count()}")
        for i in range(torch.cuda.device_count()):
            print(f"device {i}: {torch.cuda.get_device_name(i)}")


def export_engine(pt_model: Path, engine_path: Path, imgsz: int, half: bool, device: str) -> None:
    from ultralytics import YOLO

    model = YOLO(str(pt_model.expanduser().resolve()))
    result = model.export(
        format="engine",
        imgsz=imgsz,
        half=half,
        device=device,
    )
    exported = Path(str(result)).expanduser().resolve()
    engine_path = engine_path.expanduser().resolve()
    if exported != engine_path:
        engine_path.parent.mkdir(parents=True, exist_ok=True)
        exported.replace(engine_path)
    print(f"TensorRT engine ready: {engine_path}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="V3 cup approach: color YOLO + RealSense depth + TTL.")
    parser.add_argument("interface", nargs="?", default="eth0", help="Unitree SDK interface, обычно eth0.")
    parser.add_argument("--real", action="store_true", help="Отправлять команды реальному роботу.")
    parser.add_argument("--motion-backend", choices=("udp", "sdk"), default="udp")
    parser.add_argument("--udp-host", default="127.0.0.1")
    parser.add_argument("--udp-port", type=int, default=15000)
    parser.add_argument("--keep-external-processes", action="store_true", help="Не останавливать receiver/arm-player при завершении теста.")
    parser.add_argument("--command-ttl", type=float, default=0.55, help="Сколько секунд команда считается свежей.")
    parser.add_argument("--udp-repeat", type=float, default=0.05)
    parser.add_argument("--max-time", type=float, default=60.0, help="Максимальное время подхода. 0 или меньше = без ограничения.")
    parser.add_argument("--lost-timeout", type=float, default=30.0)
    parser.add_argument("--dt", type=float, default=0.05)
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_FILE), help="Файл подробного event-log. Пустая строка отключает file log.")
    parser.add_argument("--quiet", action="store_true", help="Не печатать per-frame status и dry-run SEND; profile-логи остаются.")
    parser.add_argument("--no-coffee-request", action="store_true", help="Не просить кофе голосом, если кружка не найдена на старте.")
    parser.add_argument("--coffee-request-repeat", type=float, default=5.0, help="Повторять просьбу кофе каждые N секунд до первой детекции.")
    parser.add_argument("--cup-initial-search-vx", type=float, default=0.0, help="Если стакан не найден на старте, идти вперед с этой скоростью до первой детекции.")
    parser.add_argument("--cup-initial-search-vyaw", type=float, default=0.0, help="Yaw во время стартового поиска стакана.")
    parser.add_argument("--tts-url", default="http://192.168.1.102/api/audio/tts")
    parser.add_argument("--coffee-request-text", default="дайте мне кофе")
    parser.add_argument("--lost-coffee-text", default="я потерял кофе, покажите его")
    parser.add_argument("--look-around-text", default="осматриваюсь")
    parser.add_argument("--lost-direction-seek", type=float, default=5.0)
    parser.add_argument("--lost-direction-vyaw", type=float, default=0.22)
    parser.add_argument("--lost-direction-min-vyaw", type=float, default=0.10)
    parser.add_argument("--lost-scan-delay", type=float, default=5.0)
    parser.add_argument("--lost-scan-angle", type=float, default=360.0)
    parser.add_argument("--lost-scan-vyaw", type=float, default=0.30)
    parser.add_argument("--tts-voice", default="")
    parser.add_argument("--tts-volume", type=int, default=85)
    parser.add_argument("--tts-amplification-db", type=float, default=0.0)
    parser.add_argument("--tts-timeout", type=float, default=3.0)
    parser.add_argument("--no-head-emotions", action="store_true", help="Отключить эмоции головы Arduino/Nano.")
    parser.add_argument("--head-ws-url", default="ws://esp32-control.local:81/", help="WebSocket URL ESP32-головы.")
    parser.add_argument("--head-brightness", type=int, default=120, help="Яркость эмоций головы 0..255.")
    parser.add_argument("--head-loading-speed", type=int, default=120, help="Скорость анимации загрузки головы, мс на шаг.")
    parser.add_argument("--no-conversation", action="store_true", help="Не ждать обращение 'Кузьмич' перед поиском стакана.")
    parser.add_argument("--conversation-system-prompt", default=str(Path(__file__).resolve().parents[1] / "voice_robot" / "kuzmich_system_prompt.txt"))
    parser.add_argument("--conversation-system-prompt-max-chars", type=int, default=3000, help="0 = передавать весь system prompt; для gemma4:e2b рабочий лимит около 3000.")
    parser.add_argument("--conversation-no-speech-timeout", type=float, default=10.0)
    parser.add_argument("--conversation-sample-rate", type=int, default=48000)
    parser.add_argument("--conversation-sound-threshold", type=float, default=0.015)
    parser.add_argument("--conversation-silence-after-speech", type=float, default=1.2)
    parser.add_argument("--conversation-input-device", default="0", help="Индекс или имя input-устройства sounddevice; 0 = новый USB Composite/Jieli микрофон.")
    parser.add_argument("--config", default=str(Path(__file__).resolve().parents[1] / "voice_robot" / "kuzmich.ini"), help="Путь к INI-конфигу (AI, voice, tts, head).")

    parser.add_argument("--face-recognition", action="store_true", help="Перед поиском кофе распознать человека через face_recognize.py.")
    parser.add_argument("--face-required", action="store_true", help="Остановить миссию, если face-recognition не сработал.")
    parser.add_argument("--face-backend", choices=("trt", "cpu"), default="trt", help="Backend распознавания лиц: trt = GPU TensorRT, cpu = insightface/ONNXRuntime CPU.")
    parser.add_argument("--face-python", default="", help="Python для face_recognize.py; пусто = V3/.venv_face/bin/python или текущий Python.")
    parser.add_argument("--face-script", default=str(Path(__file__).resolve().parents[1] / "face_recognize.py"))
    parser.add_argument("--face-trt-script", default=str(Path(__file__).resolve().parents[1] / "face_recognize_trt.py"))
    parser.add_argument("--face-embeddings", default=str(Path(__file__).resolve().parents[1] / "oleg_embeddings.npz"))
    parser.add_argument("--face-threshold", type=float, default=0.35)
    parser.add_argument("--face-timeout", type=float, default=30.0)
    parser.add_argument("--face-det-size", type=int, default=640, choices=(320, 640))
    parser.add_argument("--face-debug-dir", default="/tmp/face_debug")
    parser.add_argument("--no-face-tts", action="store_true", help="Не озвучивать приветствие в face_recognize.py.")
    parser.add_argument("--follow-oleg-after-grasp", action="store_true", help="После захвата стакана идти к Олегу Сироте по face-recognition+depth.")
    parser.add_argument("--follow-oleg-embeddings", default=str(Path(__file__).resolve().parents[1] / "oleg_embeddings.npz"))
    parser.add_argument("--follow-oleg-det-engine", default="", help="SCRFD TensorRT engine; пусто = ~/.insightface/models/buffalo_l/det_10g.engine.")
    parser.add_argument("--follow-oleg-rec-engine", default="", help="ArcFace TensorRT engine; пусто = ~/.insightface/models/buffalo_l/w600k_r50.engine.")
    parser.add_argument("--follow-oleg-threshold", type=float, default=0.35)
    parser.add_argument("--follow-oleg-min-face-score", type=float, default=0.50)
    parser.add_argument("--follow-oleg-stop-distance", type=float, default=1.00)
    parser.add_argument("--follow-oleg-max-time", type=float, default=90.0)
    parser.add_argument("--follow-oleg-lost-timeout", type=float, default=20.0)
    parser.add_argument("--follow-oleg-x-tolerance", type=float, default=0.14)
    parser.add_argument("--follow-oleg-target-x", type=float, default=0.0)
    parser.add_argument("--follow-oleg-z-tolerance", type=float, default=0.10)
    parser.add_argument("--follow-oleg-max-vx", type=float, default=0.25)
    parser.add_argument("--follow-oleg-max-vyaw", type=float, default=0.28)
    parser.add_argument("--follow-oleg-angle-tolerance-deg", type=float, default=5.0)
    parser.add_argument("--follow-oleg-k-yaw", type=float, default=0.90)
    parser.add_argument("--follow-oleg-min-depth-samples", type=int, default=20)
    parser.add_argument("--follow-oleg-initial-search-vx", type=float, default=0.0, help="Если Олег не найден на старте, идти вперед с этой скоростью до первой детекции.")
    parser.add_argument("--follow-oleg-initial-search-vyaw", type=float, default=0.0, help="Yaw во время стартового поиска Олега.")
    parser.add_argument("--follow-oleg-left-turn-after-s", type=float, default=0.0, help="Один раз во время подхода к Олегу остановиться через N секунд и повернуть влево.")
    parser.add_argument("--follow-oleg-left-turn-deg", type=float, default=0.0)
    parser.add_argument("--follow-oleg-left-turn-vyaw", type=float, default=0.18)
    parser.add_argument("--follow-oleg-text", default="Олег, я не вижу вас")
    parser.add_argument("--follow-oleg-reached-text", default="здравствуй хозяин, я принес вам кофе")
    parser.add_argument("--follow-oleg-take-coffee-text", default="заберите у меня кофе")
    parser.add_argument("--follow-oleg-take-coffee-repeat", type=int, default=2)
    parser.add_argument("--follow-oleg-take-coffee-repeat-delay", type=float, default=3.0)
    parser.add_argument("--follow-oleg-release-delay", type=float, default=0.0)
    parser.add_argument("--follow-oleg-reached-repeat", type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument("--follow-oleg-reached-repeat-delay", type=float, default=3.0, help=argparse.SUPPRESS)
    parser.add_argument("--no-follow-oleg-tts", action="store_true")
    parser.add_argument("--post-delivery-right-turn-deg", type=float, default=0.0, help="После доставки повернуться вправо на N градусов.")
    parser.add_argument("--post-delivery-left-turn-deg", type=float, default=0.0, help="После правого поворота повернуться влево на N градусов.")
    parser.add_argument("--post-delivery-turn-vyaw", type=float, default=0.18, help="Модуль yaw-скорости для финальных поворотов.")
    parser.add_argument("--post-delivery-right-text", default="", help="Фраза после финального правого поворота.")
    parser.add_argument("--post-delivery-left-text", default="", help="Фраза после финального левого поворота.")
    parser.add_argument("--post-delivery-right-speech-wait", type=float, default=0.0, help="Пауза после правой финальной фразы, чтобы TTS успел доиграть.")
    parser.add_argument("--post-delivery-left-speech-wait", type=float, default=0.0, help="Пауза после левой финальной фразы, чтобы TTS успел доиграть.")
    parser.add_argument("--start-companion-after-delivery", action="store_true", help="После доставки кофе запустить kuzmich_companion.py и больше не ходить.")
    parser.add_argument("--companion-ready-text", default="")
    parser.add_argument("--companion-script", default=str(Path(__file__).resolve().parents[1] / "voice_robot" / "kuzmich_companion.py"))
    parser.add_argument("--companion-python", default=str(Path(__file__).resolve().parents[1] / ".venv_voice" / "bin" / "python"))

    parser.add_argument("--model", default=str(DEFAULT_MODEL_PATH), help="Путь к .engine или .pt модели.")
    parser.add_argument("--target-class", default="cup")
    parser.add_argument("--img-size", type=int, default=960)
    parser.add_argument("--conf", type=float, default=0.05)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--allow-cpu", action="store_true", help="Не падать, если CUDA не найдена.")
    parser.add_argument("--check-gpu", action="store_true")
    parser.add_argument("--export-engine", action="store_true")
    parser.add_argument("--pt-model", default=str(DEFAULT_PT_MODEL_PATH))
    parser.add_argument("--engine-path", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--export-img-size", type=int, default=960)
    parser.add_argument("--no-half", action="store_true")

    parser.add_argument("--color-width", type=int, default=1280)
    parser.add_argument("--color-height", type=int, default=720)
    parser.add_argument("--color-fps", type=int, default=30)
    parser.add_argument("--depth-width", type=int, default=640)
    parser.add_argument("--depth-height", type=int, default=480)
    parser.add_argument("--depth-fps", type=int, default=30)
    parser.add_argument("--disable-ir-emitter", action="store_true")
    parser.add_argument("--min-depth", type=float, default=0.20)
    parser.add_argument("--max-depth", type=float, default=4.00)
    parser.add_argument("--depth-roi-ratio", type=float, default=0.45)
    parser.add_argument("--debug-dir", default=str(DEFAULT_DEBUG_DIR))
    parser.add_argument("--save-debug", action="store_true")
    parser.add_argument("--profile-every", type=int, default=0, help="Логировать задержки vision-узлов каждые N detect-циклов.")

    parser.add_argument("--stop-distance", type=float, default=0.55)
    parser.add_argument("--x-tolerance", type=float, default=0.08)
    parser.add_argument("--target-x", type=float, default=-0.08, help="Desired cup X offset in camera frame; negative means robot stops slightly right of cup.")
    parser.add_argument("--z-tolerance", type=float, default=0.04)
    parser.add_argument("--min-detection-confidence", type=float, default=0.05)
    parser.add_argument("--min-depth-samples", type=int, default=800)
    parser.add_argument("--max-z-jump", type=float, default=0.35)
    parser.add_argument("--approach-y-min", type=float, default=-0.38)
    parser.add_argument("--approach-y-max", type=float, default=-0.10)
    parser.add_argument("--max-vx", type=float, default=0.35)
    parser.add_argument("--max-vyaw", type=float, default=0.32)
    parser.add_argument("--angle-tolerance-deg", type=float, default=3.0)
    parser.add_argument("--k-yaw", type=float, default=1.05)
    parser.add_argument("--yaw-sign", type=float, default=-1.0)
    parser.add_argument("--no-arm-reach", action="store_true")
    parser.add_argument("--arm-port", type=int, default=15001)
    parser.add_argument("--arm-player-config", default=str(Path.home() / "arm_player.cfg"))
    parser.add_argument("--arm-action-timeout", type=float, default=20.0)
    parser.add_argument("--arm-reach-delay", type=float, default=0.0)
    parser.add_argument("--arm-hold", type=float, default=3.0)
    parser.add_argument("--post-grasp-backup-s", type=float, default=1.3)
    parser.add_argument("--post-grasp-backup-vx", type=float, default=-0.12)
    parser.add_argument("--post-grasp-turn-right-deg", type=float, default=0.0, help="После захвата/отступления повернуться вправо на N градусов перед поиском Олега.")
    parser.add_argument("--post-grasp-turn-vyaw", type=float, default=0.30, help="Модуль yaw-скорости для post-grasp turn.")
    parser.add_argument("--post-grasp-turn-from-start-zero", action="store_true", help="Доворачивать после захвата до yaw=-N градусов относительно старта миссии.")
    parser.add_argument("--pregrasp-distance", type=float, default=1.00)
    parser.add_argument("--no-pregrasp-before-approach", action="store_true")
    parser.add_argument("--arm-reach-action", default="left_cup_pregrasp_high_side_20260710")
    parser.add_argument("--arm-grasp-action", default="left_cup_finish_high_side_grasp_20260710")
    parser.add_argument("--arm-body-action", default="left_hold_wrist_only_cup_level_20260709")
    parser.add_argument("--arm-close-action", default="close")
    parser.add_argument("--arm-open-action", default="open")
    parser.add_argument("--arm-extend-action", default="extend")
    parser.add_argument("--arm-home-action", default="home")
    parser.add_argument("--release-after-grasp", action="store_true", help="После body раскрыть кисть и вернуть руку home.")
    parser.add_argument("--home-after-grasp", action="store_true", help="После body опустить руку home, не раскрывая кисть.")
    parser.add_argument("--hand-diagnostics", action="store_true", help="Перед захватом вывести подробную диагностику кисти/arm-player.")
    parser.add_argument("--no-direct-left-hand", action="store_true")
    parser.add_argument("--left-hand-ip", default="192.168.123.210")
    parser.add_argument("--left-hand-port", type=int, default=6000)
    parser.add_argument("--left-hand-timeout", type=float, default=2.0)
    parser.add_argument("--left-hand-open-angle", type=int, default=1000)
    parser.add_argument("--left-hand-close-angle", type=int, default=650)
    parser.add_argument("--left-first-finger-close-angle", type=int, default=None)
    parser.add_argument("--left-thumb-rotation-angle", type=int, default=350)
    parser.add_argument("--no-close-vision-check", action="store_true", help="Unsafe: close without the final camera check.")
    parser.add_argument("--close-check-frames", type=int, default=8)
    parser.add_argument("--close-check-min-good", type=int, default=3)
    parser.add_argument("--close-check-max-abs-x", type=float, default=0.14)
    parser.add_argument("--close-check-z-margin", type=float, default=0.06)
    parser.add_argument("--close-check-y-min", type=float, default=-0.30)
    parser.add_argument("--close-check-y-max", type=float, default=0.35)
    parser.add_argument("--task-complete-text", default="задача выполнена")
    parser.add_argument("--no-task-complete-tts", action="store_true")
    return parser


def ensure_follow_oleg_python(args) -> None:
    if not getattr(args, "follow_oleg_after_grasp", False):
        return
    missing = []
    for module_name in ("pycuda.driver", "insightface"):
        try:
            __import__(module_name)
        except Exception as exc:
            missing.append(f"{module_name}: {exc}")
    if not missing:
        return

    script_dir = Path(__file__).resolve().parents[1]
    face_python = script_dir / ".venv_face" / "bin" / "python"
    if not face_python.exists() or os.environ.get("V3_FACE_PYTHON_REEXEC") == "1":
        raise RuntimeError(
            "--follow-oleg-after-grasp needs pycuda and insightface in the active Python. "
            "Missing: " + "; ".join(missing)
        )

    print(
        "[INFO] Re-executing with .venv_face Python for --follow-oleg-after-grasp: "
        f"{face_python}",
        flush=True,
    )
    os.environ["V3_FACE_PYTHON_REEXEC"] = "1"
    os.environ["V3_SKIP_CONVERSATION_GATE"] = "1"
    os.execv(str(face_python), [str(face_python), str(script_dir / "itog_v3.py"), *sys.argv[1:]])


def main() -> None:
    args = build_arg_parser().parse_args()
    log_handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        log_path = Path(args.log_file).expanduser().resolve()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        handlers=log_handlers,
        force=True,
    )
    if args.log_file:
        logging.info("Event log file: %s", Path(args.log_file).expanduser().resolve())

    if args.check_gpu:
        print_gpu_info()
        if not args.export_engine:
            return

    if args.export_engine:
        export_engine(
            pt_model=Path(args.pt_model),
            engine_path=Path(args.engine_path),
            imgsz=args.export_img_size,
            half=not args.no_half,
            device=args.device,
        )
        return

    # Load INI config for AI backend
    voice_cfg = Config()
    voice_cfg.load(args.config)
    voice_cfg.on_reload(lambda: reconcile_llama_server(voice_cfg))
    voice_cfg.setup_reload_signal()
    ensure_llama_server(voice_cfg)
    llm_client = make_llm_client(voice_cfg)
    llm_generate = llm_client.generate

    run_conversation_gate(
        ConversationConfig(
            enabled=not args.no_conversation and os.environ.get("V3_SKIP_CONVERSATION_GATE") != "1",
            system_prompt_path=args.conversation_system_prompt,
            system_prompt_max_chars=args.conversation_system_prompt_max_chars,
            no_speech_timeout_s=args.conversation_no_speech_timeout,
            sample_rate=args.conversation_sample_rate,
            sound_threshold=args.conversation_sound_threshold,
            silence_after_speech_s=args.conversation_silence_after_speech,
            input_device=args.conversation_input_device or None,
        ),
        tts_url=args.tts_url,
        tts_voice=args.tts_voice or None,
        tts_volume=args.tts_volume,
        tts_amplification_db=args.tts_amplification_db,
        tts_timeout_s=args.tts_timeout,
        llm_fn=llm_generate,
    )

    ensure_follow_oleg_python(args)

    run_face_recognition_gate(
        FaceRecognitionConfig(
            enabled=args.face_recognition,
            required=args.face_required,
            backend=args.face_backend,
            python_path=args.face_python or None,
            script_path=Path(args.face_script),
            trt_script_path=Path(args.face_trt_script),
            embeddings_path=Path(args.face_embeddings),
            threshold=args.face_threshold,
            timeout_s=args.face_timeout,
            det_size=args.face_det_size,
            debug_dir=Path(args.face_debug_dir),
            headless=True,
            no_tts=args.no_face_tts,
        ),
        tts_url=args.tts_url,
        tts_volume=args.tts_volume,
        tts_amplification_db=args.tts_amplification_db,
        tts_timeout_s=args.tts_timeout,
    )

    vision_config = VisionConfig(
        model_path=Path(args.model),
        target_class=args.target_class,
        img_size=args.img_size,
        conf=args.conf,
        device=args.device,
        require_gpu=not args.allow_cpu,
        color_width=args.color_width,
        color_height=args.color_height,
        color_fps=args.color_fps,
        depth_width=args.depth_width,
        depth_height=args.depth_height,
        depth_fps=args.depth_fps,
        disable_ir_emitter=args.disable_ir_emitter,
        min_depth_m=args.min_depth,
        max_depth_m=args.max_depth,
        depth_roi_ratio=args.depth_roi_ratio,
        save_debug=args.save_debug,
        debug_dir=Path(args.debug_dir),
        profile_every=args.profile_every,
    )

    detector = YoloDepthCupDetector(vision_config)
    robot_sender = UnitreeG1VelocitySender(
        RobotVelocityConfig(
            interface=args.interface,
            dry_run=not args.real,
            dry_run_verbose=not args.quiet,
            motion_backend=args.motion_backend,
            udp_host=args.udp_host,
            udp_port=args.udp_port,
            udp_repeat_s=args.udp_repeat,
            command_ttl_s=args.command_ttl,
        )
    )
    controller = CupApproachController(
        stop_distance_m=args.stop_distance,
        x_tolerance_m=args.x_tolerance,
        target_x_m=args.target_x,
        z_tolerance_m=args.z_tolerance,
        max_vx=args.max_vx,
        max_vyaw=args.max_vyaw,
        angle_tolerance_rad=math.radians(args.angle_tolerance_deg),
        k_yaw=args.k_yaw,
        yaw_sign=args.yaw_sign,
    )

    head: RobotHead | None = None
    cup_detector_released = False
    mission_yaw_estimate_rad = 0.0
    mission_yaw_last_t: float | None = None

    def release_cup_detector() -> None:
        nonlocal cup_detector_released
        if cup_detector_released:
            return
        try:
            detector.release()
        finally:
            cup_detector_released = True

    def track_mission_yaw(vyaw: float, dt_s: float) -> None:
        nonlocal mission_yaw_estimate_rad
        if not math.isfinite(vyaw) or not math.isfinite(dt_s):
            return
        mission_yaw_estimate_rad += float(vyaw) * max(0.0, float(dt_s))

    def set_head_emotion(label: str, command) -> None:
        if head is None:
            return
        try:
            response = command(head)
            emit_event(f"HEAD {label}: {response}")
        except Exception as exc:
            logging.warning("Head emotion %s failed: %s", label, exc)

    def drive_for_duration(vx: float, vy: float, vyaw: float, duration_s: float, label: str) -> None:
        duration_s = max(0.0, float(duration_s))
        if duration_s <= 0.0:
            return
        emit_event(
            f"{label}: vx={vx:+.3f} vy={vy:+.3f} vyaw={vyaw:+.3f} "
            f"for {duration_s:.2f}s."
        )
        end_t = time.monotonic() + duration_s
        while time.monotonic() < end_t:
            track_mission_yaw(vyaw, 0.05)
            robot_sender.send(vx, vy, vyaw)
            time.sleep(0.05)
        for _ in range(5):
            robot_sender.send(0.0, 0.0, 0.0)
            time.sleep(0.05)

    def turn_right_after_grasp() -> None:
        angle_deg = max(0.0, float(args.post_grasp_turn_right_deg))
        vyaw = abs(float(args.post_grasp_turn_vyaw))
        if angle_deg <= 0.0 or vyaw <= 1e-4:
            return
        if args.post_grasp_turn_from_start_zero:
            target_yaw_rad = -math.radians(angle_deg)
            delta_yaw_rad = target_yaw_rad - mission_yaw_estimate_rad
            if delta_yaw_rad > 0.0:
                emit_event(
                    "Post-grasp zero-relative turn skipped to avoid left turn: "
                    f"yaw={math.degrees(mission_yaw_estimate_rad):+.1f} deg target={-angle_deg:.1f} deg"
                )
                return
            if abs(delta_yaw_rad) <= math.radians(2.0):
                emit_event(
                    "Post-grasp zero-relative turn skipped: "
                    f"yaw={math.degrees(mission_yaw_estimate_rad):+.1f} deg target={-angle_deg:.1f} deg"
                )
                return
            turn_vyaw = math.copysign(vyaw, delta_yaw_rad)
            duration_s = abs(delta_yaw_rad) / vyaw
            drive_for_duration(
                0.0,
                0.0,
                turn_vyaw,
                duration_s,
                (
                    f"Post-grasp zero-relative turn to {-angle_deg:.0f} deg "
                    f"from yaw={math.degrees(mission_yaw_estimate_rad):+.1f} deg"
                ),
            )
            return
        duration_s = math.radians(angle_deg) / vyaw
        drive_for_duration(0.0, 0.0, -vyaw, duration_s, f"Post-grasp right turn {angle_deg:.0f} deg")

    def speak_mission_text(text: str, label: str) -> None:
        text = str(text).strip()
        if not text:
            return
        ok = speak_text(
            tts_url=args.tts_url,
            text=text,
            voice=args.tts_voice or None,
            hardware_volume=args.tts_volume,
            amplification_db=args.tts_amplification_db,
            timeout_s=args.tts_timeout,
        )
        if ok:
            emit_event(f'TTS {label}: "{text}"')

    def run_post_delivery_sequence() -> None:
        vyaw = abs(float(args.post_delivery_turn_vyaw))
        right_deg = max(0.0, float(args.post_delivery_right_turn_deg))
        left_deg = max(0.0, float(args.post_delivery_left_turn_deg))

        if right_deg > 0.0 and vyaw > 1e-4:
            duration_s = math.radians(right_deg) / vyaw
            drive_for_duration(0.0, 0.0, -vyaw, duration_s, f"Post-delivery right turn {right_deg:.0f} deg")
        speak_mission_text(args.post_delivery_right_text, "post-delivery-right")
        right_wait = max(0.0, float(args.post_delivery_right_speech_wait))
        if right_wait > 0.0:
            emit_event(f"Waiting {right_wait:.1f}s after post-delivery right speech.")
            time.sleep(right_wait)

        if left_deg > 0.0 and vyaw > 1e-4:
            duration_s = math.radians(left_deg) / vyaw
            drive_for_duration(0.0, 0.0, vyaw, duration_s, f"Post-delivery left turn {left_deg:.0f} deg")
        speak_mission_text(args.post_delivery_left_text, "post-delivery-left")
        left_wait = max(0.0, float(args.post_delivery_left_speech_wait))
        if left_wait > 0.0:
            emit_event(f"Waiting {left_wait:.1f}s after post-delivery left speech.")
            time.sleep(left_wait)

    def run_companion_after_delivery() -> None:
        if not args.start_companion_after_delivery:
            return
        companion_script = Path(args.companion_script).expanduser().resolve()
        companion_python = Path(args.companion_python).expanduser()
        if not companion_python.is_absolute():
            companion_python = (Path.cwd() / companion_python).absolute()
        if not companion_script.exists():
            emit_event(f"Companion not started: script not found: {companion_script}")
            return
        if not companion_python.exists():
            companion_python = Path(sys.executable)
        robot_sender.stop()
        if args.companion_ready_text:
            ok = speak_text(
                tts_url=args.tts_url,
                text=args.companion_ready_text,
                voice=args.tts_voice or None,
                hardware_volume=args.tts_volume,
                amplification_db=args.tts_amplification_db,
                timeout_s=args.tts_timeout,
            )
            if ok:
                emit_event(f'TTS companion-ready: "{args.companion_ready_text}"')
        emit_event(f"Starting companion mode: {companion_python} {companion_script}")
        subprocess.run(
            [
                str(companion_python),
                str(companion_script),
                "--tts-url",
                "http://127.0.0.1/api/audio/tts",
                "--tts-timeout",
                str(max(float(args.tts_timeout), 15.0)),
                "--tts-amplification-db",
                str(args.tts_amplification_db),
                "--tts-volume",
                str(args.tts_volume),
            ],
            cwd=str(companion_script.parent),
            check=False,
        )

    try:
        if not args.no_head_emotions:
            try:
                head = RobotHead(HeadConfig(ws_url=args.head_ws_url))
                head.open()
                set_head_emotion(
                    "loading",
                    lambda h: head_loading_blue(
                        h,
                        brightness=args.head_brightness,
                        speed_ms=args.head_loading_speed,
                    ),
                )
            except Exception as exc:
                logging.warning("Head emotions disabled: %s", exc)
                head = None

        robot_sender.connect()
        system = CupApproachSystem(detector=detector, controller=controller)

        def request_coffee() -> None:
            if args.no_coffee_request:
                return
            ok = speak_text(
                tts_url=args.tts_url,
                text=args.coffee_request_text,
                voice=args.tts_voice or None,
                hardware_volume=args.tts_volume,
                amplification_db=args.tts_amplification_db,
                timeout_s=args.tts_timeout,
            )
            if ok:
                emit_event(f'TTS: "{args.coffee_request_text}"')

        def report_lost_coffee() -> None:
            set_head_emotion(
                "anger",
                lambda h: head_anger(h, brightness=args.head_brightness),
            )
            ok = speak_text(
                tts_url=args.tts_url,
                text=args.lost_coffee_text,
                voice=args.tts_voice or None,
                hardware_volume=args.tts_volume,
                amplification_db=args.tts_amplification_db,
                timeout_s=args.tts_timeout,
            )
            if ok:
                emit_event(f'TTS: "{args.lost_coffee_text}"')

        def report_look_around() -> None:
            set_head_emotion(
                "loading_blue",
                lambda h: head_loading_blue(
                    h,
                    brightness=args.head_brightness,
                    speed_ms=args.head_loading_speed,
                ),
            )
            ok = speak_text(
                tts_url=args.tts_url,
                text=args.look_around_text,
                voice=args.tts_voice or None,
                hardware_volume=args.tts_volume,
                amplification_db=args.tts_amplification_db,
                timeout_s=args.tts_timeout,
            )
            if ok:
                emit_event(f'TTS: "{args.look_around_text}"')

        arm_prepared_for_grasp = False

        def prepare_left_arm_before_final_approach() -> bool:
            nonlocal arm_prepared_for_grasp
            if args.no_arm_reach:
                return False
            if arm_prepared_for_grasp:
                return True

            arm_player_config = Path(args.arm_player_config).expanduser()
            if args.hand_diagnostics:
                log_hand_runtime_state("before_pregrasp", arm_player_config)

            if not args.no_direct_left_hand:
                open_thumb_back = [
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                ]
                if not inspire_e2_write_left_angles(
                    open_thumb_back,
                    host=args.left_hand_ip,
                    port=args.left_hand_port,
                    timeout_s=args.left_hand_timeout,
                    label="pregrasp_open_left_thumb_back",
                    settle_s=0.5,
                ):
                    emit_event("HAND MODBUS pregrasp open/thumb-back failed.")
                    return False

            if args.arm_reach_action:
                emit_event(f"ARM pregrasp action: {args.arm_reach_action}")
                if not send_arm_action(args.arm_reach_action, port=args.arm_port, timeout_s=args.arm_action_timeout):
                    emit_event(f"ARM pregrasp action failed: {args.arm_reach_action}")
                    return False

            arm_prepared_for_grasp = True
            emit_event("Pregrasp complete: left arm is raised before final approach.")
            return True

        def close_vision_check() -> bool:
            if args.no_close_vision_check:
                emit_event("WARNING: final close vision check disabled.")
                return True

            good = 0
            last_reason = "no frames"
            frames = max(1, int(args.close_check_frames))
            min_good = max(1, int(args.close_check_min_good))
            z_min = max(0.05, args.stop_distance - args.close_check_z_margin)
            z_max = args.stop_distance + args.close_check_z_margin

            for i in range(frames):
                try:
                    check_det = detector.detect()
                except Exception as exc:
                    last_reason = f"detect error: {exc}"
                    time.sleep(0.05)
                    continue

                if check_det is None:
                    last_reason = "cup not detected"
                    time.sleep(0.05)
                    continue

                ok_x = abs(check_det.X_m) <= args.close_check_max_abs_x
                ok_y = args.close_check_y_min <= check_det.Y_m <= args.close_check_y_max
                ok_z = z_min <= check_det.Z_m <= z_max
                emit_event(
                    f"Close vision check {i + 1}/{frames}: "
                    f"X={check_det.X_m:+.3f} Y={check_det.Y_m:+.3f} Z={check_det.Z_m:.3f} "
                    f"ok_x={int(ok_x)} ok_y={int(ok_y)} ok_z={int(ok_z)}"
                )

                if ok_x and ok_y and ok_z:
                    good += 1
                    if good >= min_good:
                        emit_event(f"Close vision check passed: {good}/{frames} good frames.")
                        return True
                else:
                    last_reason = (
                        f"outside safe window: X={check_det.X_m:+.3f}, "
                        f"Y={check_det.Y_m:+.3f}, Z={check_det.Z_m:.3f}"
                    )
                time.sleep(0.05)

            emit_event(
                f"Close aborted: vision check failed ({good}/{frames} good, "
                f"need {min_good}). Last reason: {last_reason}"
            )
            return False

        def run_left_hand_grasp_after_reached(det=None) -> None:
            nonlocal arm_prepared_for_grasp
            set_head_emotion(
                "friendliness",
                lambda h: head_friendliness(h, brightness=args.head_brightness),
            )
            if args.no_arm_reach:
                return
            arm_player_config = Path(args.arm_player_config).expanduser()
            if args.arm_reach_delay > 0.0:
                emit_event(f"Цель достигнута. Жду {args.arm_reach_delay:.1f}s перед захватом левой рукой.")
                time.sleep(args.arm_reach_delay)
            else:
                emit_event("Цель достигнута. Сразу запускаю захват левой рукой.")
            if args.hand_diagnostics:
                log_hand_runtime_state("before_grasp_sequence", arm_player_config)

            if not arm_prepared_for_grasp and not args.no_direct_left_hand:
                open_thumb_back = [
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                ]
                if not inspire_e2_write_left_angles(
                    open_thumb_back,
                    host=args.left_hand_ip,
                    port=args.left_hand_port,
                    timeout_s=args.left_hand_timeout,
                    label="open_left_thumb_back",
                    settle_s=0.5,
                ):
                    emit_event("HAND MODBUS open/thumb-back failed, grasp sequence stopped.")
                    return

            if not arm_prepared_for_grasp and args.arm_reach_action:
                emit_event(f"ARM action: {args.arm_reach_action}")
                if not send_arm_action(args.arm_reach_action, port=args.arm_port, timeout_s=args.arm_action_timeout):
                    emit_event(f"ARM action failed, grasp sequence stopped: {args.arm_reach_action}")
                    return
                arm_prepared_for_grasp = True

            if not args.no_direct_left_hand:
                if det is not None:
                    emit_event(
                        f"Close vision check skipped: using reached detection "
                        f"X={det.X_m:+.3f} Y={det.Y_m:+.3f} Z={det.Z_m:.3f}"
                    )
                elif not close_vision_check():
                    return

            if args.arm_grasp_action:
                emit_event(f"ARM final side-grasp action: {args.arm_grasp_action}")
                if not send_arm_action(args.arm_grasp_action, port=args.arm_port, timeout_s=args.arm_action_timeout):
                    emit_event(f"ARM final side-grasp failed, grasp sequence stopped: {args.arm_grasp_action}")
                    return

            if not args.no_direct_left_hand:
                open_thumb_side = [
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                    args.left_thumb_rotation_angle,
                ]
                if not inspire_e2_write_left_angles(
                    open_thumb_side,
                    host=args.left_hand_ip,
                    port=args.left_hand_port,
                    timeout_s=args.left_hand_timeout,
                    label="open_left_thumb_side",
                    settle_s=0.5,
                ):
                    emit_event("HAND MODBUS thumb-side failed, grasp sequence stopped.")
                    return

                first_finger_close_angle = (
                    args.left_first_finger_close_angle
                    if args.left_first_finger_close_angle is not None
                    else min(1000, args.left_hand_close_angle + 50)
                )
                close_angles = [
                    first_finger_close_angle,
                    args.left_hand_close_angle,
                    max(0, args.left_hand_close_angle - 200),
                    args.left_hand_close_angle,
                    min(1000, args.left_hand_close_angle + 150),
                    args.left_thumb_rotation_angle,
                ]
                emit_event(f"HAND MODBUS close_left angles={close_angles}")
                if not inspire_e2_write_left_angles(
                    close_angles,
                    host=args.left_hand_ip,
                    port=args.left_hand_port,
                    timeout_s=args.left_hand_timeout,
                    label="close_left",
                ):
                    emit_event("HAND MODBUS close failed, grasp sequence stopped.")
                    return
            elif args.arm_close_action:
                emit_event(f"ARM action: {args.arm_close_action}")
                if not send_arm_action(args.arm_close_action, port=args.arm_port, timeout_s=args.arm_action_timeout):
                    emit_event(f"ARM action failed, grasp sequence stopped: {args.arm_close_action}")
                    return

            if args.post_grasp_backup_s > 0.0:
                emit_event(
                    f"Post-grasp backup: vx={args.post_grasp_backup_vx:+.3f} "
                    f"for {args.post_grasp_backup_s:.1f}s before lowering arm."
                )
                end_t = time.monotonic() + args.post_grasp_backup_s
                while time.monotonic() < end_t:
                    robot_sender.send(args.post_grasp_backup_vx, 0.0, 0.0)
                    time.sleep(0.05)
                for _ in range(5):
                    robot_sender.send(0.0, 0.0, 0.0)
                    time.sleep(0.05)

            if args.arm_body_action:
                emit_event(f"ARM action: {args.arm_body_action}")
                if not send_arm_action(args.arm_body_action, port=args.arm_port, timeout_s=args.arm_action_timeout):
                    emit_event(f"ARM action failed, grasp sequence stopped: {args.arm_body_action}")
                    return

            emit_event(f"Левая кисть удерживает стакан у корпуса {args.arm_hold:.1f}s.")
            time.sleep(max(0.0, args.arm_hold))

            if not args.release_after_grasp:
                if args.home_after_grasp and args.arm_home_action:
                    emit_event(f"ARM action: {args.arm_home_action}")
                    if not send_arm_action(args.arm_home_action, port=args.arm_port, timeout_s=args.arm_action_timeout):
                        emit_event(f"ARM action failed: {args.arm_home_action}")
                        return
                    emit_event("Захват завершен: кисть держит стакан, рука опущена home.")
                    return
                emit_event("Захват завершен: кисть держит стакан, руку оставляю у корпуса.")
                return

            if not args.no_direct_left_hand:
                open_angles = [
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                    args.left_hand_open_angle,
                ]
                if not inspire_e2_write_left_angles(
                    open_angles,
                    host=args.left_hand_ip,
                    port=args.left_hand_port,
                    timeout_s=args.left_hand_timeout,
                    label="open_left",
                ):
                    emit_event("HAND MODBUS open failed.")
                    return
            elif args.arm_open_action:
                emit_event(f"ARM action: {args.arm_open_action}")
                if not send_arm_action(args.arm_open_action, port=args.arm_port, timeout_s=args.arm_action_timeout):
                    emit_event(f"ARM action failed: {args.arm_open_action}")
                    return

            if args.arm_home_action:
                emit_event(f"ARM action: {args.arm_home_action}")
                if not send_arm_action(args.arm_home_action, port=args.arm_port, timeout_s=args.arm_action_timeout):
                    emit_event(f"ARM action failed: {args.arm_home_action}")
                    return

            if args.no_task_complete_tts:
                return
            ok = speak_text(
                tts_url=args.tts_url,
                text=args.task_complete_text,
                voice=args.tts_voice or None,
                hardware_volume=args.tts_volume,
                amplification_db=args.tts_amplification_db,
                timeout_s=args.tts_timeout,
            )
            if ok:
                emit_event(f'TTS: "{args.task_complete_text}"')

        def send_cup_velocity(vx: float, vy: float, vyaw: float) -> None:
            nonlocal mission_yaw_last_t
            now = time.monotonic()
            if mission_yaw_last_t is not None:
                track_mission_yaw(vyaw, now - mission_yaw_last_t)
            mission_yaw_last_t = now
            robot_sender.send(vx, vy, vyaw)

        cup_reached = system.approach_loop(
            send_velocity=send_cup_velocity,
            dt=args.dt,
            max_time_s=args.max_time,
            lost_timeout_s=args.lost_timeout,
            on_initial_target_missing=request_coffee,
            initial_target_missing_repeat_s=args.coffee_request_repeat,
            initial_search_vx=args.cup_initial_search_vx,
            initial_search_vyaw=args.cup_initial_search_vyaw,
            on_target_lost=report_lost_coffee,
            lost_direction_seek_s=args.lost_direction_seek,
            lost_direction_seek_vyaw=args.lost_direction_vyaw,
            lost_direction_min_vyaw=args.lost_direction_min_vyaw,
            lost_scan_delay_s=args.lost_scan_delay,
            on_lost_scan=report_look_around,
            lost_scan_angle_deg=args.lost_scan_angle,
            lost_scan_vyaw=args.lost_scan_vyaw,
            on_pregrasp=None if args.no_pregrasp_before_approach else prepare_left_arm_before_final_approach,
            pregrasp_distance_m=args.pregrasp_distance,
            on_reached=run_left_hand_grasp_after_reached,
            min_detection_confidence=args.min_detection_confidence,
            min_depth_samples=args.min_depth_samples,
            max_z_jump_m=args.max_z_jump,
            approach_y_min=args.approach_y_min,
            approach_y_max=args.approach_y_max,
            target_label="кружка",
            verbose=not args.quiet,
        )

        if cup_reached and args.follow_oleg_after_grasp:
            emit_event("Cup grasp completed. Switching target from cup to Oleg Sirota.")
            for _ in range(5):
                robot_sender.send(0.0, 0.0, 0.0)
                time.sleep(0.05)
            turn_right_after_grasp()
            release_cup_detector()

            oleg_detector = None
            delivery_complete = False
            try:
                oleg_vision_config = VisionConfig(
                    model_path=Path(args.model),
                    target_class="oleg",
                    img_size=args.img_size,
                    conf=args.conf,
                    device=args.device,
                    require_gpu=not args.allow_cpu,
                    color_width=args.color_width,
                    color_height=args.color_height,
                    color_fps=args.color_fps,
                    depth_width=args.depth_width,
                    depth_height=args.depth_height,
                    depth_fps=args.depth_fps,
                    disable_ir_emitter=args.disable_ir_emitter,
                    min_depth_m=args.min_depth,
                    max_depth_m=args.max_depth,
                    depth_roi_ratio=args.depth_roi_ratio,
                    save_debug=False,
                    debug_dir=Path(args.debug_dir),
                    profile_every=args.profile_every,
                )
                oleg_detector = OlegDepthFaceDetector(
                    oleg_vision_config,
                    embeddings_path=Path(args.follow_oleg_embeddings),
                    threshold=args.follow_oleg_threshold,
                    det_engine=Path(args.follow_oleg_det_engine) if args.follow_oleg_det_engine else None,
                    rec_engine=Path(args.follow_oleg_rec_engine) if args.follow_oleg_rec_engine else None,
                    min_face_score=args.follow_oleg_min_face_score,
                )
                oleg_controller = CupApproachController(
                    stop_distance_m=args.follow_oleg_stop_distance,
                    x_tolerance_m=args.follow_oleg_x_tolerance,
                    target_x_m=args.follow_oleg_target_x,
                    z_tolerance_m=args.follow_oleg_z_tolerance,
                    max_vx=args.follow_oleg_max_vx,
                    max_vyaw=args.follow_oleg_max_vyaw,
                    angle_tolerance_rad=math.radians(args.follow_oleg_angle_tolerance_deg),
                    k_yaw=args.follow_oleg_k_yaw,
                    yaw_sign=args.yaw_sign,
                )
                oleg_system = CupApproachSystem(detector=oleg_detector, controller=oleg_controller)
                follow_oleg_missing_spoken = False

                def report_oleg_missing() -> None:
                    nonlocal follow_oleg_missing_spoken
                    if args.no_follow_oleg_tts:
                        return
                    if follow_oleg_missing_spoken:
                        return
                    follow_oleg_missing_spoken = True
                    ok = speak_text(
                        tts_url=args.tts_url,
                        text=args.follow_oleg_text,
                        voice=args.tts_voice or None,
                        hardware_volume=args.tts_volume,
                        amplification_db=args.tts_amplification_db,
                        timeout_s=args.tts_timeout,
                    )
                    if ok:
                        emit_event(f'TTS: "{args.follow_oleg_text}"')

                def report_oleg_reached(_det=None) -> None:
                    set_head_emotion(
                        "friendliness",
                        lambda h: head_friendliness(h, brightness=args.head_brightness),
                    )
                    if not args.no_follow_oleg_tts:
                        ok = speak_text(
                            tts_url=args.tts_url,
                            text=args.follow_oleg_reached_text,
                            voice=args.tts_voice or None,
                            hardware_volume=args.tts_volume,
                            amplification_db=args.tts_amplification_db,
                            timeout_s=args.tts_timeout,
                        )
                        if ok:
                            emit_event(f'TTS: "{args.follow_oleg_reached_text}"')

                        repeat_count = max(1, int(args.follow_oleg_take_coffee_repeat))
                        initial_delay = max(0.0, args.follow_oleg_take_coffee_repeat_delay)
                        if initial_delay > 0.0:
                            emit_event(f"Waiting {initial_delay:.1f}s before take-coffee request.")
                            time.sleep(initial_delay)
                        for i in range(repeat_count):
                            ok = speak_text(
                                tts_url=args.tts_url,
                                text=args.follow_oleg_take_coffee_text,
                                voice=args.tts_voice or None,
                                hardware_volume=args.tts_volume,
                                amplification_db=args.tts_amplification_db,
                                timeout_s=args.tts_timeout,
                            )
                            if ok:
                                emit_event(f'TTS take-coffee {i + 1}/{repeat_count}: "{args.follow_oleg_take_coffee_text}"')
                            if i + 1 < repeat_count:
                                time.sleep(max(0.0, args.follow_oleg_take_coffee_repeat_delay))

                    release_delay = max(0.0, args.follow_oleg_release_delay)
                    if release_delay > 0.0:
                        emit_event(f"Holding coffee {release_delay:.1f}s before release.")
                        time.sleep(release_delay)

                    emit_event("Oleg reached: releasing left hand before home.")
                    if not args.no_direct_left_hand:
                        open_angles = [
                            args.left_hand_open_angle,
                            args.left_hand_open_angle,
                            args.left_hand_open_angle,
                            args.left_hand_open_angle,
                            args.left_hand_open_angle,
                            args.left_hand_open_angle,
                        ]
                        if not inspire_e2_write_left_angles(
                            open_angles,
                            host=args.left_hand_ip,
                            port=args.left_hand_port,
                            timeout_s=args.left_hand_timeout,
                            label="follow_oleg_open_left",
                        ):
                            emit_event("HAND MODBUS follow-oleg open failed.")
                    elif args.arm_open_action:
                        emit_event(f"ARM action: {args.arm_open_action}")
                        if not send_arm_action(args.arm_open_action, port=args.arm_port, timeout_s=args.arm_action_timeout):
                            emit_event(f"ARM action failed: {args.arm_open_action}")

                    if args.arm_home_action:
                        emit_event(f"ARM action: {args.arm_home_action}")
                        if not send_arm_action(args.arm_home_action, port=args.arm_port, timeout_s=args.arm_action_timeout):
                            emit_event(f"ARM action failed: {args.arm_home_action}")

                oleg_walk_start_t = time.monotonic()
                oleg_left_turn_done = False

                def send_oleg_velocity(vx: float, vy: float, vyaw: float) -> None:
                    nonlocal oleg_left_turn_done
                    turn_after_s = max(0.0, float(args.follow_oleg_left_turn_after_s))
                    moving = abs(vx) > 1e-4 or abs(vy) > 1e-4 or abs(vyaw) > 1e-4
                    if (
                        not oleg_left_turn_done
                        and turn_after_s > 0.0
                        and moving
                        and time.monotonic() - oleg_walk_start_t >= turn_after_s
                    ):
                        oleg_left_turn_done = True
                        angle_deg = max(0.0, float(args.follow_oleg_left_turn_deg))
                        turn_vyaw = abs(float(args.follow_oleg_left_turn_vyaw))
                        for _ in range(5):
                            robot_sender.send(0.0, 0.0, 0.0)
                            time.sleep(0.05)
                        if angle_deg > 0.0 and turn_vyaw > 1e-4:
                            duration_s = math.radians(angle_deg) / turn_vyaw
                            drive_for_duration(
                                0.0,
                                0.0,
                                turn_vyaw,
                                duration_s,
                                f"One-time follow-Oleg left turn {angle_deg:.0f} deg",
                            )
                    robot_sender.send(vx, vy, vyaw)

                delivery_complete = oleg_system.approach_loop(
                    send_velocity=send_oleg_velocity,
                    dt=args.dt,
                    max_time_s=args.follow_oleg_max_time,
                    lost_timeout_s=args.follow_oleg_lost_timeout,
                    on_initial_target_missing=report_oleg_missing,
                    initial_target_missing_repeat_s=args.coffee_request_repeat,
                    initial_search_vx=args.follow_oleg_initial_search_vx,
                    initial_search_vyaw=args.follow_oleg_initial_search_vyaw,
                    on_target_lost=report_oleg_missing,
                    lost_direction_seek_s=args.lost_direction_seek,
                    lost_direction_seek_vyaw=args.lost_direction_vyaw,
                    lost_direction_min_vyaw=args.lost_direction_min_vyaw,
                    lost_scan_delay_s=args.lost_scan_delay,
                    on_lost_scan=report_look_around,
                    lost_scan_angle_deg=args.lost_scan_angle,
                    lost_scan_vyaw=args.lost_scan_vyaw,
                    on_reached=report_oleg_reached,
                    min_detection_confidence=args.follow_oleg_threshold,
                    min_depth_samples=args.follow_oleg_min_depth_samples,
                    max_z_jump_m=1.00,
                    approach_y_min=-10.0,
                    approach_y_max=10.0,
                    target_label="Олег",
                    verbose=not args.quiet,
                )
            finally:
                if oleg_detector is not None:
                    oleg_detector.release()
            if delivery_complete:
                run_post_delivery_sequence()
                run_companion_after_delivery()
    finally:
        try:
            robot_sender.stop()
        finally:
            try:
                release_cup_detector()
            finally:
                try:
                    if head is not None:
                        head.close()
                finally:
                    if not args.keep_external_processes:
                        stop_external_robot_processes()
