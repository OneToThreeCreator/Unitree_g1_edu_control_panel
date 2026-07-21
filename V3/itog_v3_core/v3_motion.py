"""Velocity control and Unitree G1 command senders."""
from __future__ import annotations

import json
import logging
import math
import socket
import threading
import time
from dataclasses import dataclass
from typing import Optional

try:
    from unitree_sdk2py.core.channel import ChannelFactoryInitialize
    from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient
except ImportError:
    ChannelFactoryInitialize = None
    LocoClient = None

from v3_common import clamp, limit_rate
from v3_vision import Detection3D

@dataclass
class VelocityCommand:
    vx: float = 0.0
    vy: float = 0.0
    vyaw: float = 0.0
    reached: bool = False
    reason: str = ""


class CupApproachController:
    def __init__(
        self,
        stop_distance_m: float = 0.50,
        x_tolerance_m: float = 0.10,
        target_x_m: float = 0.0,
        z_tolerance_m: float = 0.04,
        angle_tolerance_rad: float = math.radians(3.0),
        slow_angle_rad: float = math.radians(25.0),
        stop_turn_angle_rad: float = math.radians(45.0),
        max_vx: float = 0.25,
        max_vy: float = 0.08,
        max_vyaw: float = 0.22,
        max_ax: float = 1.00,
        max_ay: float = 0.50,
        max_ayaw: float = 2.00,
        k_forward: float = 0.60,
        k_yaw: float = 0.80,
        yaw_sign: float = -1.0,
        use_strafe: bool = False,
        vy_sign: float = 1.0,
        k_strafe: float = 0.50,
        allow_backward: bool = False,
    ):
        self.stop_distance_m = stop_distance_m
        self.x_tolerance_m = x_tolerance_m
        self.target_x_m = target_x_m
        self.z_tolerance_m = z_tolerance_m
        self.angle_tolerance_rad = angle_tolerance_rad
        self.slow_angle_rad = slow_angle_rad
        self.stop_turn_angle_rad = stop_turn_angle_rad
        self.max_vx = max_vx
        self.max_vy = max_vy
        self.max_vyaw = max_vyaw
        self.max_ax = max_ax
        self.max_ay = max_ay
        self.max_ayaw = max_ayaw
        self.k_forward = k_forward
        self.k_yaw = k_yaw
        self.yaw_sign = yaw_sign
        self.use_strafe = use_strafe
        self.vy_sign = vy_sign
        self.k_strafe = k_strafe
        self.allow_backward = allow_backward
        self.prev_cmd = VelocityCommand()

    def reset(self) -> None:
        self.prev_cmd = VelocityCommand()

    def stop(self, reason: str) -> VelocityCommand:
        self.prev_cmd = VelocityCommand(reason=reason)
        return self.prev_cmd

    def compute_command(self, det: Optional[Detection3D], dt: float) -> VelocityCommand:
        if det is None:
            return self.stop("target_lost")

        x_m = float(det.X_m)
        z_m = float(det.Z_m)
        if not math.isfinite(x_m) or not math.isfinite(z_m) or z_m <= 0:
            return self.stop("bad_depth")

        z_error = z_m - self.stop_distance_m
        x_error = x_m - self.target_x_m
        angle_to_cup = math.atan2(x_error, z_m)
        reached = (
            abs(z_error) <= self.z_tolerance_m
            and abs(x_error) <= self.x_tolerance_m
            and abs(angle_to_cup) <= self.angle_tolerance_rad
        )
        if reached:
            self.prev_cmd = VelocityCommand(reached=True, reason="reached")
            return self.prev_cmd

        if abs(angle_to_cup) > self.angle_tolerance_rad:
            vyaw = clamp(self.yaw_sign * self.k_yaw * angle_to_cup, -self.max_vyaw, self.max_vyaw)
        else:
            vyaw = 0.0

        if z_error > self.z_tolerance_m:
            vx = clamp(self.k_forward * z_error, 0.0, self.max_vx)
        elif self.allow_backward and z_error < -self.z_tolerance_m:
            vx = clamp(self.k_forward * z_error, -self.max_vx * 0.4, 0.0)
        else:
            vx = 0.0

        if abs(angle_to_cup) > self.stop_turn_angle_rad:
            vx = 0.0
        elif abs(angle_to_cup) > self.slow_angle_rad:
            vx = min(vx, 0.05)

        if self.use_strafe and abs(x_error) > self.x_tolerance_m:
            vy = clamp(self.vy_sign * self.k_strafe * x_error, -self.max_vy, self.max_vy)
        else:
            vy = 0.0

        dt = max(0.02, float(dt))
        vx = limit_rate(vx, self.prev_cmd.vx, self.max_ax * dt)
        vy = limit_rate(vy, self.prev_cmd.vy, self.max_ay * dt)
        vyaw = limit_rate(vyaw, self.prev_cmd.vyaw, self.max_ayaw * dt)

        self.prev_cmd = VelocityCommand(vx=vx, vy=vy, vyaw=vyaw, reason="moving")
        return self.prev_cmd


@dataclass
class RobotVelocityConfig:
    interface: str
    dry_run: bool = True
    dry_run_verbose: bool = True
    motion_backend: str = "udp"
    udp_host: str = "127.0.0.1"
    udp_port: int = 15000
    udp_repeat_s: float = 0.05
    command_ttl_s: float = 0.80
    timeout_s: float = 10.0
    command_duration_s: float = 0.12
    max_vx: float = 0.30
    max_vy: float = 0.10
    max_vyaw: float = 0.40


class UnitreeG1VelocitySender:
    def __init__(self, config: RobotVelocityConfig):
        self.config = config
        self.client = None
        self.sock = None
        self._udp_cmd = (0.0, 0.0, 0.0)
        self._udp_cmd_time = 0.0
        self._udp_lock = threading.Lock()
        self._udp_stop = threading.Event()
        self._udp_thread = None

    def connect(self) -> None:
        if self.config.dry_run:
            print(f"[DRY-RUN] Unitree G1 connect(interface={self.config.interface})")
            return

        if self.config.motion_backend == "udp":
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp_stop.clear()
            self._udp_thread = threading.Thread(target=self._udp_send_loop, daemon=True)
            self._udp_thread.start()
            print(
                "[OK] UDP motion backend connected: "
                f"udp://{self.config.udp_host}:{self.config.udp_port} ttl={self.config.command_ttl_s:.2f}s"
            )
            return

        if ChannelFactoryInitialize is None or LocoClient is None:
            raise RuntimeError("unitree_sdk2py не найден. Активируй окружение с Unitree SDK.")

        ChannelFactoryInitialize(0, self.config.interface)
        self.client = LocoClient()
        self.client.SetTimeout(self.config.timeout_s)
        self.client.Init()
        print("[OK] Unitree G1 LocoClient connected")

    def _udp_send_loop(self) -> None:
        while not self._udp_stop.is_set():
            now = time.monotonic()
            with self._udp_lock:
                vx, vy, vyaw = self._udp_cmd
                age = now - self._udp_cmd_time

            if age > self.config.command_ttl_s:
                vx, vy, vyaw = 0.0, 0.0, 0.0

            packet = json.dumps({"vx": vx, "vy": vy, "wz": vyaw}).encode("utf-8")
            try:
                self.sock.sendto(packet, (self.config.udp_host, self.config.udp_port))
            except OSError as exc:
                logging.warning("UDP motion send failed: %s", exc)

            time.sleep(self.config.udp_repeat_s)

    def send(self, vx: float, vy: float, vyaw: float) -> None:
        vx = clamp(float(vx), -self.config.max_vx, self.config.max_vx)
        vy = clamp(float(vy), -self.config.max_vy, self.config.max_vy)
        vyaw = clamp(float(vyaw), -self.config.max_vyaw, self.config.max_vyaw)

        if self.config.dry_run:
            if self.config.dry_run_verbose:
                print(f"SEND: vx={vx:+.3f}, vy={vy:+.3f}, vyaw={vyaw:+.3f}")
            return

        if self.config.motion_backend == "udp":
            if self.sock is None:
                raise RuntimeError("Сначала вызови robot_sender.connect()")
            with self._udp_lock:
                self._udp_cmd = (vx, vy, vyaw)
                self._udp_cmd_time = time.monotonic()
            print(f"UDP SET: vx={vx:+.3f}, vy={vy:+.3f}, wz={vyaw:+.3f}")
            return

        if self.client is None:
            raise RuntimeError("Сначала вызови robot_sender.connect()")
        if abs(vx) < 1e-4 and abs(vy) < 1e-4 and abs(vyaw) < 1e-4:
            self.client.StopMove()
        else:
            self.client.SetVelocity(vx, vy, vyaw, self.config.command_duration_s)

    def stop(self) -> None:
        if self.config.dry_run:
            if self.config.dry_run_verbose:
                print("SEND: vx=+0.000, vy=+0.000, vyaw=+0.000")
            return

        if self.config.motion_backend == "udp":
            if self.sock is not None:
                with self._udp_lock:
                    self._udp_cmd = (0.0, 0.0, 0.0)
                    self._udp_cmd_time = time.monotonic()

                packet = json.dumps({"vx": 0.0, "vy": 0.0, "wz": 0.0}).encode("utf-8")
                for _ in range(5):
                    self.sock.sendto(packet, (self.config.udp_host, self.config.udp_port))
                    time.sleep(0.02)

                self._udp_stop.set()
                if self._udp_thread is not None:
                    self._udp_thread.join(timeout=1.0)

                self.sock.close()
                self.sock = None
            return

        if self.client is not None:
            self.client.StopMove()
