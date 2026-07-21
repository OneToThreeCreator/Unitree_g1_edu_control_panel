"""
Face recognition script for Unitree G1 robot.

Uses RealSense camera + insightface to detect faces and identify a registered
person (e.g. Oleg). Speaks a greeting via TTS when recognized.

Usage:
    python face_recognize.py --embeddings oleg_embeddings.npz
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recognize a registered person via RealSense + insightface."
    )
    parser.add_argument(
        "--embeddings",
        required=True,
        help="Path to .npz file with registered embeddings.",
    )
    parser.add_argument(
        "--tts-url",
        default="http://192.168.1.102/api/audio/tts",
        help="TTS endpoint URL.",
    )
    parser.add_argument("--tts-volume", type=int, default=85)
    parser.add_argument("--tts-amplification-db", type=float, default=0.0)
    parser.add_argument("--tts-timeout", type=float, default=3.0)
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.45,
        help="Cosine similarity threshold for recognition (default: 0.45).",
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=5.0,
        help="Seconds between TTS greetings for the same person (default: 5.0).",
    )
    parser.add_argument(
        "--det-size",
        type=int,
        default=640,
        choices=[320, 640],
        help="Detection input size (default: 640).",
    )
    parser.add_argument(
        "--camera-width",
        type=int,
        default=1280,
    )
    parser.add_argument(
        "--camera-height",
        type=int,
        default=720,
    )
    parser.add_argument(
        "--camera-fps",
        type=int,
        default=30,
    )
    parser.add_argument(
        "--no-tts",
        action="store_true",
        help="Disable TTS (visual output only).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Headless mode: save debug frames to files instead of OpenCV window.",
    )
    parser.add_argument(
        "--debug-dir",
        default="debug_frames",
        help="Directory for debug frames in headless mode (default: debug_frames).",
    )
    return parser


# ---------------------------------------------------------------------------
# Insightface init
# ---------------------------------------------------------------------------

def load_insightface(det_size: int = 640):
    from insightface.app import FaceAnalysis

    app = FaceAnalysis(
        name="buffalo_l",
        providers=["CUDAExecutionProvider", "CPUExecutionProvider"],
    )
    app.prepare(ctx_id=0, det_size=(det_size, det_size))
    return app


# ---------------------------------------------------------------------------
# RealSense camera (simplified — color only)
# ---------------------------------------------------------------------------

class RealSenseCamera:
    def __init__(self, width: int = 1280, height: int = 720, fps: int = 30):
        import pyrealsense2 as rs

        self.rs = rs
        self.pipeline = rs.pipeline()
        self.rs_config = rs.config()
        self.rs_config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)

    def start(self) -> None:
        self.pipeline.start(self.rs_config)
        logging.info("RealSense color camera started")

    def read(self) -> np.ndarray:
        frames = self.pipeline.wait_for_frames()
        color_frame = frames.get_color_frame()
        if not color_frame:
            raise RuntimeError("Failed to get color frame from RealSense")
        return np.asanyarray(color_frame.get_data())

    def release(self) -> None:
        self.pipeline.stop()


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------

def speak_text(
    text: str,
    tts_url: str = "http://192.168.1.102/api/audio/tts",
    volume: int = 85,
    amplification_db: float = 0.0,
    timeout_s: float = 3.0,
) -> bool:
    try:
        payload = {
            "text": text,
            "play": True,
            "hardware_volume": volume,
            "amplification_db": amplification_db,
        }
        data = __import__("json").dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            tts_url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=timeout_s)
        body = resp.read().decode("utf-8", errors="replace")
        ok = "TTS_OK" in body or '"ok": true' in body.lower()
        if ok:
            logging.info('TTS: "%s"', text)
        else:
            logging.warning("TTS response: %s", body)
        return ok
    except Exception as exc:
        logging.warning("TTS failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Cosine similarity
# ---------------------------------------------------------------------------

def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    args = build_arg_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
    )

    # Load registered embeddings
    emb_path = Path(args.embeddings).expanduser().resolve()
    if not emb_path.exists():
        print(f"ERROR: embeddings file not found: {emb_path}", file=sys.stderr)
        sys.exit(1)

    data = np.load(str(emb_path), allow_pickle=True)
    person_name = str(data["name"])
    avg_embedding = data["avg_embedding"].astype(np.float32)
    avg_embedding = avg_embedding / np.linalg.norm(avg_embedding)
    num_registered = int(data["num_images"])

    print(f"Loaded embeddings for '{person_name}' ({num_registered} images)")
    print(f"Threshold: {args.threshold}")

    # Load insightface
    print(f"Loading insightface (det_size={args.det_size})...")
    face_app = load_insightface(args.det_size)

    # Start RealSense camera
    print("Starting RealSense camera...")
    camera = RealSenseCamera(args.camera_width, args.camera_height, args.camera_fps)
    camera.start()

    # State
    last_greet_time: dict[str, float] = {}
    frame_count = 0
    greeted_and_exit = False
    headless = args.headless
    debug_dir = Path(args.debug_dir).expanduser().resolve()
    if headless:
        debug_dir.mkdir(parents=True, exist_ok=True)
        print(f"Headless mode: saving debug frames to {debug_dir}")
    else:
        print("\nPress 'q' in the OpenCV window to quit.\n")

    try:
        while not greeted_and_exit:
            frame = camera.read()
            frame_count += 1

            faces = face_app.get(frame)

            if frame_count % 5 == 0:
                print(f"frame={frame_count} faces_detected={len(faces)}", flush=True)

            for face in faces:
                if face.det_score < 0.5:
                    continue

                bbox = face.bbox.astype(int)
                x1, y1, x2, y2 = bbox
                detected_embedding = face.embedding.astype(np.float32)
                detected_embedding = detected_embedding / np.linalg.norm(detected_embedding)

                sim = cosine_similarity(avg_embedding, detected_embedding)

                print(f"  face: sim={sim:.3f} name={'Oleg' if sim >= args.threshold else 'Unknown'}", flush=True)

                if sim >= args.threshold:
                    name = person_name
                    color = (0, 255, 0)
                else:
                    name = "Unknown"
                    color = (0, 0, 255)

                # Draw bbox + name
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                label = f"{name} ({sim:.2f})"
                cv2.putText(
                    frame, label, (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2,
                )

                # TTS greeting — only once, then exit to reduce CPU load
                if sim >= args.threshold:
                    if name not in last_greet_time:
                        last_greet_time[name] = True
                        if not args.no_tts:
                            greeting = f"Привет, {person_name}, вот ваш кофе!"
                            speak_text(
                                greeting,
                                tts_url=args.tts_url,
                                volume=args.tts_volume,
                                amplification_db=args.tts_amplification_db,
                                timeout_s=args.tts_timeout,
                            )
                        print(f"GREETED: {person_name} (sim={sim:.3f}). Exiting to reduce CPU load.", flush=True)
                        greeted_and_exit = True
                        break

            # HUD
            cv2.putText(
                frame,
                f"frame={frame_count} faces={len(faces)} threshold={args.threshold}",
                (20, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 0),
                2,
            )

            if headless:
                cv2.imwrite(str(debug_dir / "latest.jpg"), frame)
            else:
                cv2.imshow("Face Recognition — Unitree G1", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

    except KeyboardInterrupt:
        pass
    finally:
        camera.release()
        if not headless:
            cv2.destroyAllWindows()
        print("\nDone.")


if __name__ == "__main__":
    main()
