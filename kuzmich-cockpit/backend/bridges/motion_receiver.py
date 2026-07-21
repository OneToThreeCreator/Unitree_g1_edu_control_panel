"""Motion receiver: слушает UDP 15100, прокидывает команды в Unitree SDK.

Запуск:
    python -m backend.bridges.motion_receiver [--host 127.0.0.1] [--port 15100] [--interface eth0]

Кокпит шлёт JSON {vx, vy, wz} на этот порт — receiver вызывает
LocoClient.SetVelocity() для реального движения робота.
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import socket
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("motion-receiver")


def main() -> None:
    parser = argparse.ArgumentParser(description="UDP motion receiver for Unitree G1")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=15100)
    parser.add_argument("--interface", default="eth0", help="Unitree SDK network interface")
    parser.add_argument("--dry-run", action="store_true", help="Только логировать, не двигать")
    args = parser.parse_args()

    # --- Unitree SDK ---
    client = None
    if not args.dry_run:
        try:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize
            from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient

            ChannelFactoryInitialize(0, args.interface)
            client = LocoClient()
            client.SetTimeout(10.0)
            client.Init()
            log.info("Unitree LocoClient connected (interface=%s)", args.interface)
        except ImportError:
            log.error("unitree_sdk2py не найден. Установи SDK или используй --dry-run.")
            sys.exit(1)
        except Exception as exc:
            log.error("SDK init failed: %s", exc)
            sys.exit(1)
    else:
        log.info("DRY-RUN mode — velocity commands will be logged only")

    # --- UDP socket ---
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    sock.settimeout(1.0)
    log.info("Listening on UDP %s:%d", args.host, args.port)

    running = True

    def on_signal(sig, frame):
        nonlocal running
        log.info("Shutting down...")
        running = False

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    last_vx = last_vy = last_wz = 0.0
    cmd_count = 0

    while running:
        try:
            data, addr = sock.recvfrom(1024)
        except socket.timeout:
            continue
        except OSError:
            break

        try:
            pkt = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.warning("Bad packet from %s: %r", addr, data[:80])
            continue

        vx = float(pkt.get("vx", 0.0))
        vy = float(pkt.get("vy", 0.0))
        wz = float(pkt.get("wz", 0.0))

        # Avoid spamming logs for zero commands
        if vx != last_vx or vy != last_vy or wz != last_wz:
            log.info("VEL vx=%.3f vy=%.3f wz=%.3f", vx, vy, wz)
            last_vx, last_vy, last_wz = vx, vy, wz

        if args.dry_run:
            continue

        # Send to robot via SDK
        try:
            if abs(vx) < 1e-4 and abs(vy) < 1e-4 and abs(wz) < 1e-4:
                client.StopMove()
            else:
                client.SetVelocity(vx, vy, wz, 0.12)
        except Exception as exc:
            log.warning("SDK SetVelocity failed: %s", exc)

        cmd_count += 1

    # Stop on exit
    if client is not None:
        try:
            client.StopMove()
        except Exception:
            pass

    sock.close()
    log.info("Motion receiver stopped. Processed %d commands.", cmd_count)


if __name__ == "__main__":
    main()
