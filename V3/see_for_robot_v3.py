from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import math
from pathlib import Path
import threading
import time
from typing import Optional

import cv2
import numpy as np

from itog_v3 import RealSenseColorDepthCamera, TensorRTYoloEngine, VisionConfig, clamp


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_PATH = SCRIPT_DIR / "yolo11n_960.engine"


class V3LiveStream:
    def __init__(
        self,
        model_path: Path,
        img_size: int,
        conf: float,
        target_class: str,
        jpeg_quality: int,
        disable_ir_emitter: bool,
        color_width: int,
        color_height: int,
        color_fps: int,
        depth_width: int,
        depth_height: int,
        depth_fps: int,
        color_only: bool,
        depth_every: int,
        profile_every: int,
        encode_fps: float,
    ):
        self.config = VisionConfig(
            model_path=model_path,
            img_size=img_size,
            conf=conf,
            target_class=target_class,
            disable_ir_emitter=disable_ir_emitter,
            require_gpu=True,
            color_width=color_width,
            color_height=color_height,
            color_fps=color_fps,
            depth_width=depth_width,
            depth_height=depth_height,
            depth_fps=depth_fps,
        )
        self.jpeg_quality = jpeg_quality
        self.depth_every = max(1, int(depth_every))
        if color_only:
            self.camera = FastColorCamera(self.config)
        elif self.depth_every > 1:
            self.camera = FastDepthCamera(self.config)
        else:
            self.camera = RealSenseColorDepthCamera(self.config)
        self.color_only = color_only
        self.latest_metric_text = ""
        self.profile_every = max(0, int(profile_every))
        self.encode_delay_s = 1.0 / max(0.1, float(encode_fps))
        self.frame_profile_text = ""
        self.encoder_profile_text = ""
        self.engine = TensorRTYoloEngine(model_path.expanduser().resolve(), img_size, conf, target_class)
        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.latest_jpeg: Optional[bytes] = None
        self.latest_image: Optional[np.ndarray] = None
        self.latest_status = "starting"
        self.thread: Optional[threading.Thread] = None
        self.encoder_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.camera.start()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        self.encoder_thread = threading.Thread(target=self._encode_loop, daemon=True)
        self.encoder_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2.0)
        if self.encoder_thread is not None:
            self.encoder_thread.join(timeout=2.0)
        self.engine.release()
        self.camera.release()

    def snapshot(self) -> bytes:
        with self.lock:
            if self.latest_jpeg is not None:
                return self.latest_jpeg
            status = self.latest_status

        img = np.zeros((720, 1280, 3), dtype=np.uint8)
        cv2.putText(img, status, (24, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
        ok, encoded = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
        if not ok:
            raise RuntimeError("Could not encode placeholder frame")
        return encoded.tobytes()

    def _loop(self) -> None:
        last_frame_time = time.monotonic()
        smoothed_fps = 0.0

        while not self.stop_event.is_set():
            try:
                loop_t0 = time.monotonic()
                want_depth = not self.color_only and (self.depth_every <= 1 or ((self.camera.frame_id + 1) % self.depth_every == 0))
                camera_t0 = time.monotonic()
                if hasattr(self.camera, "read_fast"):
                    color, depth, frame_id = self.camera.read_fast(want_depth)
                else:
                    color, depth, frame_id = self.camera.read()
                camera_dt = time.monotonic() - camera_t0
                now = time.monotonic()
                frame_dt = max(1e-6, now - last_frame_time)
                last_frame_time = now
                current_fps = 1.0 / frame_dt
                smoothed_fps = current_fps if smoothed_fps == 0.0 else smoothed_fps * 0.9 + current_fps * 0.1

                yolo_t0 = time.monotonic()
                best = self.engine.predict_best(color)
                yolo_dt = time.monotonic() - yolo_t0

                annotated = color.copy()
                draw_t0 = time.monotonic()
                status = self._draw(annotated, depth, best, yolo_dt, smoothed_fps, frame_id)
                draw_dt = time.monotonic() - draw_t0
                total_dt = time.monotonic() - loop_t0
                self.frame_profile_text = (
                    f"camera={camera_dt*1000:.1f}ms yolo={yolo_dt*1000:.1f}ms "
                    f"draw={draw_dt*1000:.1f}ms total={total_dt*1000:.1f}ms"
                )
                if self.profile_every > 0 and frame_id % self.profile_every == 0:
                    print(f"PROFILE stream frame={frame_id} {self.frame_profile_text} | {status}", flush=True)

                with self.lock:
                    self.latest_image = annotated
                    self.latest_status = status

            except Exception as exc:
                with self.lock:
                    self.latest_status = f"stream error: {exc}"
                time.sleep(0.1)

    def _encode_loop(self) -> None:
        while not self.stop_event.is_set():
            encode_t0 = time.monotonic()
            with self.lock:
                image = None if self.latest_image is None else self.latest_image.copy()

            if image is not None:
                ok, encoded = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
                encode_dt = time.monotonic() - encode_t0
                if ok:
                    with self.lock:
                        self.latest_jpeg = encoded.tobytes()
                    self.encoder_profile_text = f"jpeg_async={encode_dt*1000:.1f}ms"
                else:
                    with self.lock:
                        self.latest_status = "stream error: Could not encode frame"

            elapsed = time.monotonic() - encode_t0
            time.sleep(max(0.0, self.encode_delay_s - elapsed))

    def _draw(
        self,
        img: np.ndarray,
        depth: Optional[np.ndarray],
        best: Optional[tuple[list[float], float, str]],
        yolo_dt: float,
        camera_fps: float,
        frame_id: int,
    ) -> str:
        h, w = img.shape[:2]
        status = "cup not found"

        if best is None:
            cv2.putText(img, status, (20, 46), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
        else:
            bbox, conf, class_name = best
            x1, y1, x2, y2 = [int(v) for v in bbox]
            u = (bbox[0] + bbox[2]) * 0.5
            v = (bbox[1] + bbox[3]) * 0.5
            if depth is None:
                x_norm = (u - w * 0.5) / max(1.0, w * 0.5)
                status = f"{class_name} conf={conf:.2f} center=({u:.1f},{v:.1f}) x_norm={x_norm:+.3f}"
            else:
                z_m, samples = self._median_depth(depth, bbox)
                intr = self.camera.color_intrinsics
                if intr is None:
                    raise RuntimeError("RealSense intrinsics not initialized")

                x_m = (u - intr.ppx) * z_m / intr.fx
                y_m = (v - intr.ppy) * z_m / intr.fy
                dist_m = math.sqrt(x_m * x_m + y_m * y_m + z_m * z_m)
                angle_deg = math.degrees(math.atan2(x_m, z_m))
                status = (
                    f"{class_name} conf={conf:.2f} X={x_m:+.3f}m Z={z_m:.3f}m "
                    f"dist={dist_m:.3f}m angle={angle_deg:+.1f}deg samples={samples}"
                )
                self.latest_metric_text = f"X={x_m:+.3f}m Z={z_m:.3f}m angle={angle_deg:+.1f}deg"

            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.circle(img, (int(u), int(v)), 5, (0, 0, 255), -1)
            cv2.putText(img, f"{class_name} {conf:.2f}", (x1, max(24, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 0), 2)
            cv2.putText(img, f"bbox={[round(v, 1) for v in bbox]}", (20, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
            cv2.putText(img, f"center=({u:.1f}, {v:.1f})", (20, 74), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
            if depth is None:
                depth_mode = "color-only fast stream" if self.color_only else f"depth every {self.depth_every} frames"
                cv2.putText(img, f"x_norm={x_norm:+.3f}  {depth_mode}", (20, 102), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                if self.latest_metric_text:
                    cv2.putText(img, f"last depth: {self.latest_metric_text}", (20, 130), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
            else:
                cv2.putText(img, f"X={x_m:+.3f}m  Z={z_m:.3f}m  angle={angle_deg:+.1f}deg", (20, 102), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

        mode = "color-only fast" if self.color_only else "color+depth"
        cv2.putText(img, f"V3 {mode} TensorRT  frame={frame_id}", (20, h - 54), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        cv2.putText(img, f"cam_fps={camera_fps:.1f} yolo_dt={yolo_dt:.3f}s {time.strftime('%H:%M:%S')}", (20, h - 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        if self.frame_profile_text:
            cv2.putText(img, self.frame_profile_text, (20, h - 84), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
        if self.encoder_profile_text:
            cv2.putText(img, self.encoder_profile_text, (20, h - 114), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2)
        return status

    def _median_depth(self, depth: np.ndarray, bbox: list[float]) -> tuple[float, int]:
        h, w = depth.shape[:2]
        x1, y1, x2, y2 = bbox
        cx = (x1 + x2) * 0.5
        cy = (y1 + y2) * 0.5
        roi_w = max(4.0, (x2 - x1) * self.config.depth_roi_ratio)
        roi_h = max(4.0, (y2 - y1) * self.config.depth_roi_ratio)
        rx1 = int(clamp(cx - roi_w * 0.5, 0, w - 1))
        rx2 = int(clamp(cx + roi_w * 0.5, 0, w))
        ry1 = int(clamp(cy - roi_h * 0.5, 0, h - 1))
        ry2 = int(clamp(cy + roi_h * 0.5, 0, h))
        roi = depth[ry1:ry2, rx1:rx2].astype(np.float32) * self.camera.depth_scale
        valid = roi[(roi >= self.config.min_depth_m) & (roi <= self.config.max_depth_m)]
        if valid.size == 0:
            raise RuntimeError("No valid depth inside bbox")
        return float(np.median(valid)), int(valid.size)


class FastColorCamera:
    def __init__(self, config: VisionConfig):
        import pyrealsense2 as rs

        self.rs = rs
        self.config = config
        self.pipeline = rs.pipeline()
        self.rs_config = rs.config()
        self.color_intrinsics = None
        self.depth_scale = 0.001
        self.frame_id = 0
        self.rs_config.enable_stream(
            rs.stream.color,
            config.color_width,
            config.color_height,
            rs.format.bgr8,
            config.color_fps,
        )

    def start(self) -> None:
        profile = self.pipeline.start(self.rs_config)
        color_stream = profile.get_stream(self.rs.stream.color).as_video_stream_profile()
        self.color_intrinsics = color_stream.get_intrinsics()

    def read(self) -> tuple[np.ndarray, Optional[np.ndarray], int]:
        frames = self.pipeline.wait_for_frames()
        color_frame = frames.get_color_frame()
        if not color_frame:
            raise RuntimeError("Не удалось получить color кадр RealSense")
        color = np.asanyarray(color_frame.get_data())
        self.frame_id += 1
        return color, None, self.frame_id

    def release(self) -> None:
        self.pipeline.stop()


class FastDepthCamera:
    def __init__(self, config: VisionConfig):
        import pyrealsense2 as rs

        self.rs = rs
        self.config = config
        self.pipeline = rs.pipeline()
        self.rs_config = rs.config()
        self.align = rs.align(rs.stream.color)
        self.depth_scale = 0.001
        self.color_intrinsics = None
        self.frame_id = 0

        self.rs_config.enable_stream(
            rs.stream.color,
            config.color_width,
            config.color_height,
            rs.format.bgr8,
            config.color_fps,
        )
        self.rs_config.enable_stream(
            rs.stream.depth,
            config.depth_width,
            config.depth_height,
            rs.format.z16,
            config.depth_fps,
        )

    def start(self) -> None:
        profile = self.pipeline.start(self.rs_config)
        device = profile.get_device()
        depth_sensor = device.first_depth_sensor()
        self.depth_scale = float(depth_sensor.get_depth_scale())

        if self.config.disable_ir_emitter:
            for option_name, value in (("emitter_enabled", 0), ("laser_power", 0)):
                option = getattr(self.rs.option, option_name, None)
                if option is not None and depth_sensor.supports(option):
                    depth_sensor.set_option(option, value)

        color_stream = profile.get_stream(self.rs.stream.color).as_video_stream_profile()
        self.color_intrinsics = color_stream.get_intrinsics()

    def read_fast(self, want_depth: bool) -> tuple[np.ndarray, Optional[np.ndarray], int]:
        frames = self.pipeline.wait_for_frames()
        if want_depth:
            frames = self.align.process(frames)

        color_frame = frames.get_color_frame()
        if not color_frame:
            raise RuntimeError("Не удалось получить color кадр RealSense")

        depth = None
        if want_depth:
            depth_frame = frames.get_depth_frame()
            if not depth_frame:
                raise RuntimeError("Не удалось получить depth кадр RealSense")
            depth = np.asanyarray(depth_frame.get_data())

        color = np.asanyarray(color_frame.get_data())
        self.frame_id += 1
        return color, depth, self.frame_id

    def release(self) -> None:
        self.pipeline.stop()


class StreamHandler(BaseHTTPRequestHandler):
    stream: V3LiveStream
    frame_delay_s: float

    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send_index()
            return
        if self.path.startswith("/snapshot.jpg"):
            self._send_snapshot()
            return
        if self.path.startswith("/stream.mjpg"):
            self._send_stream()
            return
        self.send_error(404)

    def _send_index(self) -> None:
        interval_ms = 1
        html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>Robot V3 YOLO Live</title>
  <style>
    html, body {{ margin: 0; height: 100%; background: #101010; color: #eee; font-family: sans-serif; }}
    body {{ display: flex; flex-direction: column; }}
    header {{ padding: 10px 14px; background: #1d1d1d; font-size: 15px; }}
    img {{ flex: 1; width: 100%; height: calc(100vh - 42px); object-fit: contain; }}
  </style>
</head>
<body>
  <header>Robot V3 YOLO live stream</header>
  <img id="feed" alt="Robot V3 YOLO live stream">
  <script>
    const feed = document.getElementById("feed");
    let nextTimer = null;

    function scheduleNext() {{
      clearTimeout(nextTimer);
      nextTimer = setTimeout(loadNext, {interval_ms});
    }}

    function loadNext() {{
      const img = new Image();
      img.onload = () => {{
        feed.src = img.src;
        scheduleNext();
      }};
      img.onerror = scheduleNext;
      img.src = "/snapshot.jpg?t=" + Date.now();
    }}

    loadNext();
  </script>
</body>
</html>
""".encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(html)

    def _send_snapshot(self) -> None:
        frame = self.stream.snapshot()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(frame)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(frame)

    def _send_stream(self) -> None:
        self.send_response(200)
        self.send_header("Cache-Control", "no-cache, private")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        while True:
            try:
                frame = self.stream.snapshot()
                self.wfile.write(b"--frame\r\n")
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii"))
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
                time.sleep(self.frame_delay_s)
            except (BrokenPipeError, ConnectionResetError):
                return


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="V3 RealSense color+depth + TensorRT YOLO live stream.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--model", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--img-size", type=int, default=960)
    parser.add_argument("--conf", type=float, default=0.05)
    parser.add_argument("--target-class", default="cup")
    parser.add_argument("--jpeg-quality", type=int, default=82)
    parser.add_argument("--stream-fps", type=float, default=12.0)
    parser.add_argument("--disable-ir-emitter", action="store_true")
    parser.add_argument("--color-width", type=int, default=1280)
    parser.add_argument("--color-height", type=int, default=720)
    parser.add_argument("--color-fps", type=int, default=30)
    parser.add_argument("--depth-width", type=int, default=640)
    parser.add_argument("--depth-height", type=int, default=480)
    parser.add_argument("--depth-fps", type=int, default=30)
    parser.add_argument("--color-only", action="store_true", help="Fast debug stream without depth alignment; no metric X/Z.")
    parser.add_argument("--depth-every", type=int, default=1, help="In depth mode, run RealSense depth alignment every N frames.")
    parser.add_argument("--profile-every", type=int, default=0, help="Print stream node timings every N frames.")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    stream = V3LiveStream(
        model_path=Path(args.model),
        img_size=args.img_size,
        conf=args.conf,
        target_class=args.target_class,
        jpeg_quality=args.jpeg_quality,
        disable_ir_emitter=args.disable_ir_emitter,
        color_width=args.color_width,
        color_height=args.color_height,
        color_fps=args.color_fps,
        depth_width=args.depth_width,
        depth_height=args.depth_height,
        depth_fps=args.depth_fps,
        color_only=args.color_only,
        depth_every=args.depth_every,
        profile_every=args.profile_every,
        encode_fps=args.stream_fps,
    )
    stream.start()
    StreamHandler.stream = stream
    StreamHandler.frame_delay_s = 1.0 / max(0.1, args.stream_fps)
    server = ThreadingHTTPServer((args.host, args.port), StreamHandler)

    print(f"Open this URL on your laptop: http://192.168.1.102:{args.port}/")
    print("Stop: Ctrl+C")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
        print("Stopped by user")
    finally:
        server.server_close()
        stream.stop()


if __name__ == "__main__":
    main()
