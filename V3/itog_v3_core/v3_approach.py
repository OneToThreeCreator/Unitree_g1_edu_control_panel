"""High-level cup approach finite-state loop."""
from __future__ import annotations

import logging
import math
import time
from typing import Any, Callable, Optional

from v3_common import clamp, emit_event
from v3_motion import CupApproachController
from v3_vision import Detection3D, YoloDepthCupDetector

class CupApproachSystem:
    def __init__(
        self,
        detector: YoloDepthCupDetector,
        controller: CupApproachController,
    ):
        self.detector = detector
        self.controller = controller

    @staticmethod
    def _sleep_remaining(loop_start: float, period_s: float) -> None:
        if period_s <= 0.0:
            return
        elapsed = time.monotonic() - loop_start
        time.sleep(max(0.0, period_s - elapsed))

    def approach_loop(
        self,
        send_velocity: Any,
        dt: float,
        max_time_s: float,
        lost_timeout_s: float,
        on_initial_target_missing: Optional[Callable[[], None]] = None,
        initial_target_missing_repeat_s: float = 5.0,
        initial_search_vx: float = 0.0,
        initial_search_vyaw: float = 0.0,
        on_target_lost: Optional[Callable[[], None]] = None,
        lost_direction_seek_s: float = 5.0,
        lost_direction_seek_vyaw: float = 0.22,
        lost_direction_min_vyaw: float = 0.10,
        lost_scan_delay_s: float = 5.0,
        on_lost_scan: Optional[Callable[[], None]] = None,
        lost_scan_angle_deg: float = 360.0,
        lost_scan_vyaw: float = 0.30,
        on_pregrasp: Optional[Callable[[], bool]] = None,
        pregrasp_distance_m: float = 1.00,
        on_reached: Optional[Callable[[Detection3D], None]] = None,
        min_detection_confidence: float = 0.05,
        min_depth_samples: int = 800,
        max_z_jump_m: float = 0.35,
        approach_y_min: float = -0.38,
        approach_y_max: float = -0.10,
        target_label: str = "цель",
        verbose: bool = True,
    ) -> bool:
        start_time = time.monotonic()
        last_time = start_time
        last_valid_time = start_time
        target_seen = False
        last_initial_missing_callback_time = 0.0
        target_lost_callback_done = False
        lost_scan_done = False
        lost_direction_seek_logged = False
        pregrasp_done = False
        last_depth_warning_time = 0.0
        last_reject_log_time = 0.0
        last_lost_direction_log_time = 0.0
        last_target_angle = 0.0
        last_valid_z_m: Optional[float] = None
        self.controller.reset()

        try:
            while max_time_s <= 0.0 or time.monotonic() - start_time < max_time_s:
                now = time.monotonic()
                loop_start = now
                real_dt = now - last_time
                last_time = now

                try:
                    det = self.detector.detect()
                except (ValueError, RuntimeError) as exc:
                    if now - last_depth_warning_time >= 1.0:
                        last_depth_warning_time = now
                        logging.warning("Depth/vision error: %s", exc)
                    det = None

                if det is not None:
                    reject_reason = None
                    if float(det.confidence) < float(min_detection_confidence):
                        reject_reason = f"low conf {det.confidence:.2f} < {min_detection_confidence:.2f}"
                    elif int(det.depth_samples) < int(min_depth_samples):
                        reject_reason = f"few depth samples {det.depth_samples} < {min_depth_samples}"
                    elif not (float(approach_y_min) <= float(det.Y_m) <= float(approach_y_max)):
                        reject_reason = (
                            f"Y outside cup-height band {det.Y_m:+.3f} "
                            f"not in [{approach_y_min:+.3f}, {approach_y_max:+.3f}]"
                        )
                    elif (
                        target_seen
                        and last_valid_z_m is not None
                        and float(det.Z_m) > last_valid_z_m + float(max_z_jump_m)
                    ):
                        reject_reason = (
                            f"Z jump {last_valid_z_m:.3f} -> {det.Z_m:.3f}m "
                            f"> {max_z_jump_m:.3f}m"
                        )

                    if reject_reason is not None:
                        if now - last_reject_log_time >= 1.0:
                            last_reject_log_time = now
                            emit_event(
                                f"Reject detection: {reject_reason}. "
                                f"frame={det.frame_id} X={det.X_m:+.3f} Y={det.Y_m:+.3f} "
                                f"Z={det.Z_m:.3f} samples={det.depth_samples}",
                                verbose,
                            )
                        det = None

                if det is None:
                    if not target_seen:
                        if abs(initial_search_vx) > 1e-4 or abs(initial_search_vyaw) > 1e-4:
                            send_velocity(float(initial_search_vx), 0.0, float(initial_search_vyaw))
                        else:
                            cmd = self.controller.stop("target_lost")
                            send_velocity(cmd.vx, cmd.vy, cmd.vyaw)
                        should_call_callback = (
                            on_initial_target_missing is not None
                            and (
                                last_initial_missing_callback_time <= 0.0
                                or now - last_initial_missing_callback_time >= initial_target_missing_repeat_s
                            )
                        )
                        if should_call_callback:
                            last_initial_missing_callback_time = now
                            emit_event(f"{target_label} не найдена на старте. Жду детекцию.", verbose)
                            on_initial_target_missing()
                        self._sleep_remaining(loop_start, dt)
                        continue

                    lost_s = now - last_valid_time
                    if lost_s < lost_direction_seek_s:
                        seek_vyaw = self._lost_direction_vyaw(
                            last_target_angle,
                            max_vyaw=lost_direction_seek_vyaw,
                            min_vyaw=lost_direction_min_vyaw,
                        )
                        if now - last_lost_direction_log_time >= 1.0:
                            lost_direction_seek_logged = True
                            last_lost_direction_log_time = now
                            emit_event(
                                f"Цель ушла из кадра. Доворачиваю в сторону последнего угла "
                                f"{math.degrees(last_target_angle):+.1f} deg: vyaw={seek_vyaw:+.3f}",
                                verbose,
                            )
                        send_velocity(0.0, 0.0, seek_vyaw)
                        self._sleep_remaining(loop_start, dt)
                        continue

                    cmd = self.controller.stop("target_lost")
                    send_velocity(cmd.vx, cmd.vy, cmd.vyaw)
                    if not target_lost_callback_done:
                        target_lost_callback_done = True
                        emit_event(f"Цель потеряна. Жду повторную детекцию {lost_timeout_s:.1f} секунд.", verbose)
                        if on_target_lost is not None:
                            on_target_lost()

                    if not lost_scan_done and lost_s >= lost_scan_delay_s:
                        lost_scan_done = True
                        emit_event(
                            f"Осматриваюсь: разворот на месте {lost_scan_angle_deg:.0f} градусов.",
                            verbose,
                        )
                        if on_lost_scan is not None:
                            on_lost_scan()
                        scan_det = self._scan_for_lost_target(
                            send_velocity=send_velocity,
                            dt=dt,
                            angle_deg=lost_scan_angle_deg,
                            vyaw=lost_scan_vyaw,
                            direction_vyaw=self._lost_direction_vyaw(
                                last_target_angle,
                                max_vyaw=1.0,
                                min_vyaw=1.0,
                            ),
                            verbose=verbose,
                        )
                        if scan_det is not None:
                            det = scan_det
                            now = time.monotonic()
                        else:
                            send_velocity(0.0, 0.0, 0.0)

                    if det is None and time.monotonic() - last_valid_time > lost_timeout_s:
                        emit_event("Цель не найдена повторно. Робот остановлен.", verbose)
                        return False
                    if det is None:
                        self._sleep_remaining(loop_start, dt)
                        continue

                last_valid_time = now
                target_seen = True
                last_valid_z_m = float(det.Z_m)
                target_lost_callback_done = False
                lost_scan_done = False
                lost_direction_seek_logged = False
                last_target_angle = math.atan2(float(det.X_m), float(det.Z_m))

                if (
                    not pregrasp_done
                    and on_pregrasp is not None
                    and float(det.Z_m) <= float(pregrasp_distance_m)
                    and float(det.Z_m) > self.controller.stop_distance_m + 0.05
                ):
                    for _ in range(5):
                        send_velocity(0.0, 0.0, 0.0)
                        time.sleep(0.05)
                    emit_event(
                        f"Pregrasp distance reached: Z={det.Z_m:.3f}m <= {pregrasp_distance_m:.3f}m. "
                        "Stopping to raise left arm before final approach.",
                        verbose,
                    )
                    pregrasp_done = True
                    if not on_pregrasp():
                        send_velocity(0.0, 0.0, 0.0)
                        emit_event("Pregrasp failed. Robot stopped before final approach.", verbose)
                        return False
                    self.controller.reset()
                    post_pregrasp_time = time.monotonic()
                    last_time = post_pregrasp_time
                    last_valid_time = post_pregrasp_time
                    target_lost_callback_done = False
                    lost_scan_done = False
                    last_lost_direction_log_time = 0.0
                    self._sleep_remaining(loop_start, dt)
                    continue

                cmd = self.controller.compute_command(det, real_dt)
                send_velocity(cmd.vx, cmd.vy, cmd.vyaw)

                if verbose:
                    target_age = time.monotonic() - now
                    emit_event(
                        f"frame={det.frame_id} X={det.X_m:+.3f}m Y={det.Y_m:+.3f}m Z={det.Z_m:.3f}m "
                        f"conf={det.confidence:.2f} samples={det.depth_samples} "
                        f"yolo_dt={det.yolo_dt_s:.3f}s target_age={target_age:.3f}s "
                        f"vx={cmd.vx:+.3f} vy={cmd.vy:+.3f} vyaw={cmd.vyaw:+.3f} | {cmd.reason}",
                        verbose,
                    )

                if cmd.reached:
                    for _ in range(3):
                        send_velocity(0.0, 0.0, 0.0)
                        time.sleep(0.05)
                    emit_event(f"Дошли до цели: {target_label}. Робот остановлен.", verbose)
                    if on_reached is not None:
                        on_reached(det)
                    return True

                self._sleep_remaining(loop_start, dt)

            send_velocity(0.0, 0.0, 0.0)
            emit_event("Время подхода вышло. Робот остановлен.", verbose)
            return False

        except KeyboardInterrupt:
            send_velocity(0.0, 0.0, 0.0)
            raise
        except Exception:
            try:
                send_velocity(0.0, 0.0, 0.0)
            finally:
                logging.exception("Аварийная остановка из-за ошибки")
            raise

    def _detect_or_none(self) -> Optional[Detection3D]:
        try:
            return self.detector.detect()
        except (ValueError, RuntimeError) as exc:
            logging.warning("Depth/vision error during scan: %s", exc)
            return None

    def _lost_direction_vyaw(self, angle_to_target: float, max_vyaw: float, min_vyaw: float) -> float:
        if abs(angle_to_target) < math.radians(1.0):
            return 0.0
        sign = 1.0 if self.controller.yaw_sign * angle_to_target > 0.0 else -1.0
        magnitude = clamp(abs(self.controller.k_yaw * angle_to_target), abs(min_vyaw), abs(max_vyaw))
        return sign * magnitude

    def _scan_for_lost_target(
        self,
        send_velocity: Any,
        dt: float,
        angle_deg: float,
        vyaw: float,
        direction_vyaw: float,
        verbose: bool,
    ) -> Optional[Detection3D]:
        angular_speed = max(0.05, abs(float(vyaw)))
        direction = 1.0 if direction_vyaw >= 0.0 else -1.0
        scan_vyaw = direction * angular_speed
        turn_s = math.radians(abs(float(angle_deg))) / angular_speed
        for _ in range(6):
            send_velocity(0.0, 0.0, 0.0)
            time.sleep(0.05)
        emit_event(f"scan 360: vyaw={scan_vyaw:+.3f} duration={turn_s:.2f}s", verbose)
        step_start = time.monotonic()
        while time.monotonic() - step_start < turn_s:
            loop_start = time.monotonic()
            send_velocity(0.0, 0.0, scan_vyaw)
            det = self._detect_or_none()
            if det is not None:
                send_velocity(0.0, 0.0, 0.0)
                emit_event("Кружка найдена во время осмотра.", verbose)
                return det
            self._sleep_remaining(loop_start, dt)

        send_velocity(0.0, 0.0, 0.0)
        return None
