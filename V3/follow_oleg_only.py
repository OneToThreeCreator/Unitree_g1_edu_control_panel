#!/usr/bin/env python3
"""Standalone Oleg-follow mode for V3.

Runs only the face/depth detector and walking controller. No cup search,
no arm actions, no conversation gate.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CORE_DIR = SCRIPT_DIR / "itog_v3_core"
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from v3_approach import CupApproachSystem
from v3_common import DEFAULT_LOG_FILE, DEFAULT_MODEL_PATH, emit_event, speak_text
from v3_face import OlegDepthFaceDetector
from v3_motion import CupApproachController, RobotVelocityConfig, UnitreeG1VelocitySender
from v3_vision import VisionConfig


def _has_process(pattern: str) -> bool:
    own_pid = os.getpid()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        pid = int(proc.name)
        if pid == own_pid:
            continue
        try:
            cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace")
        except OSError:
            continue
        if pattern in cmdline:
            return True
    return False


def _start_udp_receiver(args: argparse.Namespace) -> subprocess.Popen | None:
    if args.motion_backend != "udp" or not args.real:
        return None
    if _has_process("g1_sdk_udp_receiver_fsm801_cpp"):
        emit_event("UDP receiver already running.")
        return None

    receiver = Path(args.udp_receiver).expanduser()
    if not receiver.exists():
        raise FileNotFoundError(f"UDP receiver not found: {receiver}")

    log_path = Path(args.udp_receiver_log).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("ab", buffering=0)
    command = [
        str(receiver),
        "--iface",
        args.interface,
        "--udp-port",
        str(args.udp_port),
        "--fsm",
        str(args.fsm),
        "--max-linear-x",
        str(args.receiver_max_vx),
        "--max-linear-y",
        str(args.receiver_max_vy),
        "--max-angular-z",
        str(args.receiver_max_vyaw),
        "--send-rate-hz",
        str(args.receiver_rate_hz),
        "--cmd-timeout-s",
        str(args.receiver_cmd_timeout),
    ]
    proc = subprocess.Popen(
        command,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    emit_event(f"Started UDP receiver pid={proc.pid}: {' '.join(command)}")
    time.sleep(args.receiver_start_wait_s)
    if proc.poll() is not None:
        raise RuntimeError(f"UDP receiver exited with code {proc.returncode}. See {log_path}")
    return proc


def _stop_udp_receiver(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        return
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Follow Oleg only: RealSense + TensorRT face recognition + G1 motion.")
    parser.add_argument("interface", nargs="?", default="eth0")
    parser.add_argument("--real", action="store_true", help="Send motion commands to the robot.")
    parser.add_argument("--motion-backend", choices=("udp", "sdk"), default="udp")
    parser.add_argument("--udp-host", default="127.0.0.1")
    parser.add_argument("--udp-port", type=int, default=15000)
    parser.add_argument("--udp-repeat", type=float, default=0.05)
    parser.add_argument("--command-ttl", type=float, default=0.55)
    parser.add_argument("--keep-external-processes", action="store_true")

    parser.add_argument("--max-time", type=float, default=90.0)
    parser.add_argument("--lost-timeout", type=float, default=20.0)
    parser.add_argument("--dt", type=float, default=0.05)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--log-file", default=str(DEFAULT_LOG_FILE.with_name("follow_oleg_only.log")))

    parser.add_argument("--model", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--img-size", type=int, default=960)
    parser.add_argument("--conf", type=float, default=0.05)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--allow-cpu", action="store_true")
    parser.add_argument("--color-width", type=int, default=1280)
    parser.add_argument("--color-height", type=int, default=720)
    parser.add_argument("--color-fps", type=int, default=30)
    parser.add_argument("--depth-width", type=int, default=640)
    parser.add_argument("--depth-height", type=int, default=480)
    parser.add_argument("--depth-fps", type=int, default=30)
    parser.add_argument("--disable-ir-emitter", action="store_true")
    parser.add_argument("--min-depth", type=float, default=0.20)
    parser.add_argument("--max-depth", type=float, default=5.00)
    parser.add_argument("--depth-roi-ratio", type=float, default=0.45)
    parser.add_argument("--profile-every", type=int, default=10)

    parser.add_argument("--embeddings", default=str(SCRIPT_DIR / "oleg_embeddings.npz"))
    parser.add_argument("--threshold", type=float, default=0.35)
    parser.add_argument("--det-engine", default="")
    parser.add_argument("--rec-engine", default="")
    parser.add_argument("--min-face-score", type=float, default=0.50)

    parser.add_argument("--stop-distance", type=float, default=1.00)
    parser.add_argument("--x-tolerance", type=float, default=0.16)
    parser.add_argument("--target-x", type=float, default=0.0)
    parser.add_argument("--z-tolerance", type=float, default=0.10)
    parser.add_argument("--angle-tolerance-deg", type=float, default=5.0)
    parser.add_argument("--max-vx", type=float, default=0.22)
    parser.add_argument("--max-vyaw", type=float, default=0.26)
    parser.add_argument("--k-yaw", type=float, default=0.80)
    parser.add_argument("--yaw-sign", type=float, default=-1.0)
    parser.add_argument("--min-depth-samples", type=int, default=120)
    parser.add_argument("--max-z-jump", type=float, default=1.00)

    parser.add_argument("--lost-direction-seek", type=float, default=5.0)
    parser.add_argument("--lost-direction-vyaw", type=float, default=0.22)
    parser.add_argument("--lost-direction-min-vyaw", type=float, default=0.10)
    parser.add_argument("--lost-scan-delay", type=float, default=5.0)
    parser.add_argument("--lost-scan-angle", type=float, default=360.0)
    parser.add_argument("--lost-scan-vyaw", type=float, default=0.30)

    parser.add_argument("--tts-url", default="http://192.168.1.102/api/audio/tts")
    parser.add_argument("--tts-volume", type=int, default=100)
    parser.add_argument("--tts-amplification-db", type=float, default=15.0)
    parser.add_argument("--tts-timeout", type=float, default=12.0)
    parser.add_argument("--no-tts", action="store_true")
    parser.add_argument("--missing-text", default="Олег, покажитесь")
    parser.add_argument("--reached-text", default="здравствуй хозяин")

    parser.add_argument("--udp-receiver", default="/home/unitree/g1_sdk_udp_receiver_fsm801_cpp")
    parser.add_argument("--udp-receiver-log", default=str(SCRIPT_DIR / "follow_oleg_udp_receiver.log"))
    parser.add_argument("--fsm", type=int, default=801)
    parser.add_argument("--receiver-max-vx", type=float, default=0.30)
    parser.add_argument("--receiver-max-vy", type=float, default=0.00)
    parser.add_argument("--receiver-max-vyaw", type=float, default=0.30)
    parser.add_argument("--receiver-rate-hz", type=float, default=20.0)
    parser.add_argument("--receiver-cmd-timeout", type=float, default=0.90)
    parser.add_argument("--receiver-start-wait-s", type=float, default=2.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        log_path = Path(args.log_file).expanduser()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        handlers=handlers,
        force=True,
    )

    receiver_proc = None
    detector = None
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

    def say(text: str) -> None:
        if args.no_tts:
            return
        ok = speak_text(
            tts_url=args.tts_url,
            text=text,
            hardware_volume=args.tts_volume,
            amplification_db=args.tts_amplification_db,
            timeout_s=args.tts_timeout,
        )
        if ok:
            emit_event(f'TTS: "{text}"')

    try:
        receiver_proc = _start_udp_receiver(args)
        vision = VisionConfig(
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
            profile_every=args.profile_every,
        )
        detector = OlegDepthFaceDetector(
            vision,
            embeddings_path=Path(args.embeddings),
            threshold=args.threshold,
            det_engine=Path(args.det_engine) if args.det_engine else None,
            rec_engine=Path(args.rec_engine) if args.rec_engine else None,
            min_face_score=args.min_face_score,
        )
        controller = CupApproachController(
            stop_distance_m=args.stop_distance,
            x_tolerance_m=args.x_tolerance,
            target_x_m=args.target_x,
            z_tolerance_m=args.z_tolerance,
            max_vx=args.max_vx,
            max_vyaw=args.max_vyaw,
            angle_tolerance_rad=__import__("math").radians(args.angle_tolerance_deg),
            k_yaw=args.k_yaw,
            yaw_sign=args.yaw_sign,
        )
        robot_sender.connect()
        system = CupApproachSystem(detector=detector, controller=controller)
        ok = system.approach_loop(
            send_velocity=robot_sender.send,
            dt=args.dt,
            max_time_s=args.max_time,
            lost_timeout_s=args.lost_timeout,
            on_initial_target_missing=lambda: say(args.missing_text),
            initial_target_missing_repeat_s=5.0,
            on_target_lost=lambda: say(args.missing_text),
            lost_direction_seek_s=args.lost_direction_seek,
            lost_direction_seek_vyaw=args.lost_direction_vyaw,
            lost_direction_min_vyaw=args.lost_direction_min_vyaw,
            lost_scan_delay_s=args.lost_scan_delay,
            lost_scan_angle_deg=args.lost_scan_angle,
            lost_scan_vyaw=args.lost_scan_vyaw,
            on_reached=lambda _det: say(args.reached_text),
            min_detection_confidence=args.threshold,
            min_depth_samples=args.min_depth_samples,
            max_z_jump_m=args.max_z_jump,
            approach_y_min=-10.0,
            approach_y_max=10.0,
            target_label="Олег",
            verbose=not args.quiet,
        )
        return 0 if ok else 2
    finally:
        try:
            robot_sender.stop()
        finally:
            if detector is not None:
                detector.release()
            if not args.keep_external_processes:
                _stop_udp_receiver(receiver_proc)


if __name__ == "__main__":
    raise SystemExit(main())
