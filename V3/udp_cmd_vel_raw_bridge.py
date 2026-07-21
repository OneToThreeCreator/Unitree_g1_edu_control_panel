#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node


def clamp(value: float, limit: float) -> float:
    limit = abs(float(limit))
    return max(-limit, min(limit, float(value)))


class UdpCmdVelRawBridge(Node):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__("udp_cmd_vel_raw_bridge")
        self.args = args
        self.pub = self.create_publisher(Twist, args.topic, 10)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.sock.bind((args.udp_host, args.udp_port))
        self.last_rx_t = 0.0
        self.last_log_t = 0.0
        self.cmd = Twist()
        self.create_timer(1.0 / max(args.rate_hz, 1.0), self.tick)
        self.get_logger().info(
            "UDP -> ROS: udp://%s:%d -> %s max_x=%.3f max_y=%.3f max_wz=%.3f timeout=%.2f"
            % (
                args.udp_host,
                args.udp_port,
                args.topic,
                args.max_linear_x,
                args.max_linear_y,
                args.max_angular_z,
                args.cmd_timeout_s,
            )
        )

    def read_udp(self) -> None:
        while True:
            try:
                data, _ = self.sock.recvfrom(4096)
            except BlockingIOError:
                return
            try:
                packet = json.loads(data.decode("utf-8"))
                vx = clamp(packet.get("vx", 0.0), self.args.max_linear_x)
                vy = clamp(packet.get("vy", 0.0), self.args.max_linear_y)
                wz = clamp(packet.get("wz", 0.0), self.args.max_angular_z)
            except Exception as exc:
                self.get_logger().warning("bad UDP packet: %r" % (exc,))
                continue

            msg = Twist()
            msg.linear.x = vx
            msg.linear.y = vy
            msg.angular.z = wz
            self.cmd = msg
            self.last_rx_t = time.monotonic()

            now = time.monotonic()
            if abs(vx) > 1e-4 or abs(vy) > 1e-4 or abs(wz) > 1e-4 or now - self.last_log_t > 1.0:
                self.get_logger().info("UDP recv vx=%.3f vy=%.3f wz=%.3f" % (vx, vy, wz))
                self.last_log_t = now

    def tick(self) -> None:
        self.read_udp()
        if self.last_rx_t <= 0.0 or time.monotonic() - self.last_rx_t > self.args.cmd_timeout_s:
            self.pub.publish(Twist())
        else:
            self.pub.publish(self.cmd)

    def publish_stop(self, count: int = 20) -> None:
        stop = Twist()
        for _ in range(count):
            self.pub.publish(stop)
            time.sleep(0.02)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--udp-host", default="127.0.0.1")
    parser.add_argument("--udp-port", type=int, default=15000)
    parser.add_argument("--topic", default="/cmd_vel_raw")
    parser.add_argument("--max-linear-x", type=float, default=0.30)
    parser.add_argument("--max-linear-y", type=float, default=0.00)
    parser.add_argument("--max-angular-z", type=float, default=0.30)
    parser.add_argument("--rate-hz", type=float, default=20.0)
    parser.add_argument("--cmd-timeout-s", type=float, default=0.35)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rclpy.init()
    node = UdpCmdVelRawBridge(args)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.publish_stop()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
