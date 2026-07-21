#!/usr/bin/env python3
"""MJPEG proxy for an Android IP Webcam stream forwarded through ADB."""
from __future__ import annotations

import argparse
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2


class SharedFrame:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jpeg: bytes | None = None
        self.fps = 0.0
        self.shape = ""
        self.ok = False
        self.error = ""


def capture_loop(source_url: str, shared: SharedFrame, jpeg_quality: int, target_fps: float) -> None:
    frame_delay = 1.0 / max(1.0, target_fps)
    while True:
        cap = cv2.VideoCapture(source_url)
        if not cap.isOpened():
            with shared.lock:
                shared.ok = False
                shared.error = f"cannot open source: {source_url}"
            time.sleep(1.0)
            continue

        count = 0
        t0 = time.monotonic()
        while True:
            loop_t = time.monotonic()
            ret, frame = cap.read()
            if not ret or frame is None:
                with shared.lock:
                    shared.ok = False
                    shared.error = "source read failed"
                break

            ok, encoded = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), int(jpeg_quality)],
            )
            if ok:
                count += 1
                now = time.monotonic()
                if now - t0 >= 1.0:
                    fps = count / (now - t0)
                    count = 0
                    t0 = now
                else:
                    with shared.lock:
                        fps = shared.fps
                with shared.lock:
                    shared.jpeg = encoded.tobytes()
                    shared.fps = fps
                    shared.shape = f"{frame.shape[1]}x{frame.shape[0]}"
                    shared.ok = True
                    shared.error = ""

            elapsed = time.monotonic() - loop_t
            if elapsed < frame_delay:
                time.sleep(frame_delay - elapsed)

        cap.release()
        time.sleep(0.5)


def make_handler(shared: SharedFrame):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            return

        def do_GET(self) -> None:
            if self.path in {"/", "/index.html"}:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"""<!doctype html><html><head><title>Phone Camera</title>
<style>body{margin:0;background:#111;color:#eee;font-family:sans-serif}img{width:100vw;height:100vh;object-fit:contain}.bar{position:fixed;left:0;top:0;padding:8px 12px;background:#0008}</style>
</head><body><div class="bar">phone camera stream</div><img src="/stream.mjpg"></body></html>"""
                )
                return
            if self.path == "/status":
                with shared.lock:
                    body = f"ok={shared.ok} fps={shared.fps:.1f} shape={shared.shape} error={shared.error}\n".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path != "/stream.mjpg":
                self.send_error(404)
                return

            self.send_response(200)
            self.send_header("Age", "0")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()

            while True:
                with shared.lock:
                    jpeg = shared.jpeg
                if jpeg is None:
                    time.sleep(0.05)
                    continue
                try:
                    self.wfile.write(b"--frame\r\n")
                    self.wfile.write(b"Content-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                    self.wfile.write(jpeg)
                    self.wfile.write(b"\r\n")
                    time.sleep(0.03)
                except (BrokenPipeError, ConnectionResetError):
                    return

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="http://127.0.0.1:18080/video")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--quality", type=int, default=70)
    parser.add_argument("--fps", type=float, default=20.0)
    args = parser.parse_args()

    shared = SharedFrame()
    thread = threading.Thread(
        target=capture_loop,
        args=(args.source, shared, args.quality, args.fps),
        daemon=True,
    )
    thread.start()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(shared))
    print(f"phone camera stream: http://{args.host}:{args.port}/ source={args.source}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
