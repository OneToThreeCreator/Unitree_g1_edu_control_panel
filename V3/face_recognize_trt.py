"""
Face recognition using TensorRT engines directly on GPU.
Replaces insightface with native TensorRT inference for Jetson/Tegra.
"""

from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import cv2
import numpy as np
from insightface.utils import face_align

# Patch numpy for TensorRT 8.x compatibility
np.bool = np.bool_
np.int = np.int_
np.float = np.float64
np.complex = np.complex128
np.object = np.object_
np.str = np.str_

import tensorrt as trt
import pycuda.driver as cuda

TRT_LOGGER = trt.Logger(trt.Logger.WARNING)
MODELS_DIR = os.path.expanduser("~/.insightface/models/buffalo_l")
SCRFD_STRIDES = (8, 16, 32)
SCRFD_NUM_ANCHORS = 2

cuda.init()
CUDA_CONTEXT = cuda.Device(0).make_context()


def cleanup_cuda_context() -> None:
    global CUDA_CONTEXT
    if CUDA_CONTEXT is None:
        return
    try:
        CUDA_CONTEXT.pop()
    except Exception:
        pass
    try:
        CUDA_CONTEXT.detach()
    except Exception:
        pass
    CUDA_CONTEXT = None


atexit.register(cleanup_cuda_context)


# ---------------------------------------------------------------------------
# TensorRT engine loader
# ---------------------------------------------------------------------------

def load_engine(engine_path: str):
    runtime = trt.Runtime(TRT_LOGGER)
    with open(engine_path, "rb") as f:
        engine = runtime.deserialize_cuda_engine(f.read())
    return engine


def allocate_buffers(context, engine, input_shape):
    """Allocate persistent host/device buffers with a fixed input shape."""
    inputs, outputs = [], []

    for i in range(engine.num_io_tensors):
        name = engine.get_tensor_name(i)
        if engine.get_tensor_mode(name) == trt.TensorIOMode.INPUT:
            context.set_input_shape(name, input_shape)

    for i in range(engine.num_io_tensors):
        name = engine.get_tensor_name(i)
        dtype = trt.nptype(engine.get_tensor_dtype(name))
        shape = context.get_tensor_shape(name)
        host_mem = cuda.pagelocked_empty(int(np.prod(shape)), dtype=dtype).reshape(shape)
        device_mem = cuda.mem_alloc(host_mem.nbytes)
        context.set_tensor_address(name, int(device_mem))
        if engine.get_tensor_mode(name) == trt.TensorIOMode.INPUT:
            inputs.append({"name": name, "host": host_mem, "device": device_mem, "shape": shape, "dtype": dtype})
        else:
            outputs.append({"name": name, "host": host_mem, "device": device_mem, "shape": shape, "dtype": dtype})
    return inputs, outputs


def run_engine(context, inputs, outputs, input_data):
    np.copyto(inputs[0]["host"], np.ascontiguousarray(input_data, dtype=inputs[0]["dtype"]))
    cuda.memcpy_htod(inputs[0]["device"], inputs[0]["host"])
    context.execute_async_v3(0)
    cuda.Context.synchronize()

    results = []
    for out in outputs:
        cuda.memcpy_dtoh(out["host"], out["device"])
        results.append(out["host"].copy())
    return results


def nms(dets: np.ndarray, thresh: float) -> list[int]:
    if dets.size == 0:
        return []
    x1, y1, x2, y2, scores = dets[:, 0], dets[:, 1], dets[:, 2], dets[:, 3], dets[:, 4]
    areas = (x2 - x1 + 1.0) * (y2 - y1 + 1.0)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1 + 1.0)
        h = np.maximum(0.0, yy2 - yy1 + 1.0)
        inter = w * h
        overlap = inter / (areas[i] + areas[order[1:]] - inter)
        inds = np.where(overlap <= thresh)[0]
        order = order[inds + 1]
    return keep


def distance2bbox(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
    x1 = points[:, 0] - distance[:, 0]
    y1 = points[:, 1] - distance[:, 1]
    x2 = points[:, 0] + distance[:, 2]
    y2 = points[:, 1] + distance[:, 3]
    return np.stack([x1, y1, x2, y2], axis=-1)


def distance2kps(points: np.ndarray, distance: np.ndarray) -> np.ndarray:
    preds = []
    for i in range(0, distance.shape[1], 2):
        px = points[:, 0] + distance[:, i]
        py = points[:, 1] + distance[:, i + 1]
        preds.append(px)
        preds.append(py)
    return np.stack(preds, axis=-1)


def anchor_centers(height: int, width: int, stride: int) -> np.ndarray:
    grid_y, grid_x = np.mgrid[:height, :width]
    centers = np.stack((grid_x, grid_y), axis=-1).astype(np.float32)
    centers = (centers * stride).reshape((-1, 2))
    if SCRFD_NUM_ANCHORS > 1:
        centers = np.stack([centers] * SCRFD_NUM_ANCHORS, axis=1).reshape((-1, 2))
    return centers


# ---------------------------------------------------------------------------
# Face detection (RetinaFace via TensorRT)
# ---------------------------------------------------------------------------

class FaceDetectorTRT:
    def __init__(self, engine_path: str, conf_thresh: float = 0.5):
        self.conf_thresh = conf_thresh
        self.engine = load_engine(engine_path)
        self.context = self.engine.create_execution_context()
        self.input_size = 640
        self.inputs, self.outputs = allocate_buffers(self.context, self.engine, (1, 3, self.input_size, self.input_size))

    def preprocess(self, img: np.ndarray, input_size: int = 640) -> tuple[np.ndarray, float, float]:
        h, w = img.shape[:2]
        scale = input_size / max(h, w)
        new_w, new_h = int(w * scale), int(h * scale)
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        padded = np.full((input_size, input_size, 3), 114, dtype=np.uint8)
        padded[:new_h, :new_w] = resized
        blob = cv2.dnn.blobFromImage(
            padded,
            1.0 / 128.0,
            (input_size, input_size),
            (127.5, 127.5, 127.5),
            swapRB=True,
        )
        return blob.astype(np.float32), scale, 0.0

    def detect(self, img: np.ndarray) -> list[dict]:
        blob, scale, _pad = self.preprocess(img, self.input_size)
        results = run_engine(self.context, self.inputs, self.outputs, blob)

        proposals = []
        landmarks = []
        scores_out = []
        for level, stride in enumerate(SCRFD_STRIDES):
            score = results[level * 3].reshape(-1)
            bbox_pred = results[level * 3 + 1].reshape(-1, 4) * stride
            kps_pred = results[level * 3 + 2].reshape(-1, 10) * stride
            feat_h = self.input_size // stride
            feat_w = self.input_size // stride
            centers = anchor_centers(feat_h, feat_w, stride)
            valid = np.where(score >= self.conf_thresh)[0]
            if valid.size == 0:
                continue
            boxes = distance2bbox(centers, bbox_pred)[valid]
            kps = distance2kps(centers, kps_pred)[valid].reshape((-1, 5, 2))
            proposals.append(boxes)
            landmarks.append(kps)
            scores_out.append(score[valid])

        if not proposals:
            return []

        bboxes = np.vstack(proposals)
        kpss = np.vstack(landmarks)
        scores = np.concatenate(scores_out)
        dets = np.hstack((bboxes, scores[:, None])).astype(np.float32)
        keep = nms(dets, 0.4)
        h, w = img.shape[:2]
        faces = []
        for i in keep:
            x1, y1, x2, y2 = dets[i, :4] / scale
            x1 = max(0, int(x1))
            y1 = max(0, int(y1))
            x2 = min(w, int(x2))
            y2 = min(h, int(y2))
            if x2 - x1 > 10 and y2 - y1 > 10:
                faces.append(
                    {
                        "bbox": [x1, y1, x2, y2],
                        "kps": (kpss[i] / scale).astype(np.float32),
                        "score": float(scores[i]),
                    }
                )
        return faces


# ---------------------------------------------------------------------------
# Face recognition (ArcFace via TensorRT)
# ---------------------------------------------------------------------------

class FaceRecognizerTRT:
    def __init__(self, engine_path: str, input_size: int = 112):
        self.input_size = input_size
        self.engine = load_engine(engine_path)
        self.context = self.engine.create_execution_context()
        self.inputs, self.outputs = allocate_buffers(self.context, self.engine, (1, 3, 112, 112))

    def preprocess(self, img: np.ndarray) -> np.ndarray:
        return cv2.dnn.blobFromImage(
            img,
            1.0 / 128.0,
            (self.input_size, self.input_size),
            (127.5, 127.5, 127.5),
            swapRB=True,
        ).astype(np.float32)

    def get_embedding(self, face_img: np.ndarray) -> np.ndarray:
        blob = self.preprocess(face_img)
        results = run_engine(self.context, self.inputs, self.outputs, blob)
        embedding = results[0].ravel().astype(np.float32)
        embedding = embedding / np.linalg.norm(embedding)
        return embedding


# ---------------------------------------------------------------------------
# TTS
# ---------------------------------------------------------------------------

def speak_text(text: str, tts_url: str, volume: int = 85, amplification_db: float = 0.0, timeout_s: float = 3.0) -> bool:
    try:
        payload = {"text": text, "play": True, "hardware_volume": volume, "amplification_db": amplification_db}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(tts_url, data=data, headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=timeout_s)
        body = resp.read().decode("utf-8", errors="replace")
        ok = "TTS_OK" in body or '"ok": true' in body.lower()
        if ok:
            logging.info('TTS: "%s"', text)
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
# RealSense camera
# ---------------------------------------------------------------------------

class RealSenseCamera:
    def __init__(self, width: int = 1280, height: int = 720, fps: int = 30):
        import pyrealsense2 as rs
        self.pipeline = rs.pipeline()
        self.rs_config = rs.config()
        self.rs_config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)

    def start(self):
        self.pipeline.start(self.rs_config)
        logging.info("RealSense color camera started")

    def read(self) -> np.ndarray:
        import pyrealsense2 as rs
        frames = self.pipeline.wait_for_frames()
        color_frame = frames.get_color_frame()
        if not color_frame:
            raise RuntimeError("Failed to get color frame")
        return np.asanyarray(color_frame.get_data())

    def release(self):
        self.pipeline.stop()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_arg_parser():
    parser = argparse.ArgumentParser(description="Face recognition with TensorRT on GPU.")
    parser.add_argument("--embeddings", required=True, help="Path to .npz file with registered embeddings.")
    parser.add_argument("--tts-url", default="http://192.168.1.102/api/audio/tts")
    parser.add_argument("--tts-volume", type=int, default=85)
    parser.add_argument("--tts-amplification-db", type=float, default=0.0)
    parser.add_argument("--tts-timeout", type=float, default=3.0)
    parser.add_argument("--threshold", type=float, default=0.35)
    parser.add_argument("--det-engine", default=os.path.join(MODELS_DIR, "det_10g.engine"))
    parser.add_argument("--rec-engine", default=os.path.join(MODELS_DIR, "w600k_r50.engine"))
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--debug-dir", default="debug_frames")
    parser.add_argument("--camera-width", type=int, default=1280)
    parser.add_argument("--camera-height", type=int, default=720)
    parser.add_argument("--camera-fps", type=int, default=30)
    parser.add_argument("--no-tts", action="store_true")
    parser.add_argument("--max-time", type=float, default=0.0, help="Stop after N seconds; 0 means no limit.")
    parser.add_argument("--self-test", action="store_true", help="Run dummy GPU inference without opening RealSense.")
    return parser


def main():
    args = build_arg_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    # Load embeddings
    emb_path = Path(args.embeddings).expanduser().resolve()
    data = np.load(str(emb_path), allow_pickle=True)
    person_name = str(data["name"])
    avg_embedding = data["avg_embedding"].astype(np.float32)
    avg_embedding = avg_embedding / np.linalg.norm(avg_embedding)
    print(f"Loaded embeddings for '{person_name}' ({int(data['num_images'])} images)")

    # Load TensorRT engines on GPU
    print("Loading TensorRT engines on GPU...")
    detector = FaceDetectorTRT(args.det_engine)
    recognizer = FaceRecognizerTRT(args.rec_engine)
    print("TensorRT engines loaded on GPU!")

    if args.self_test:
        dummy = np.zeros((720, 1280, 3), dtype=np.uint8)
        t0 = time.time()
        faces = detector.detect(dummy)
        dummy_face = np.zeros((112, 112, 3), dtype=np.uint8)
        embedding = recognizer.get_embedding(dummy_face)
        dt_ms = (time.time() - t0) * 1000.0
        print(f"SELF_TEST_OK faces={len(faces)} emb_shape={embedding.shape} elapsed_ms={dt_ms:.1f}")
        return

    # Start camera
    camera = RealSenseCamera(args.camera_width, args.camera_height, args.camera_fps)
    camera.start()

    # State
    greeted = False
    frame_count = 0
    headless = args.headless
    debug_dir = Path(args.debug_dir).expanduser().resolve()
    if headless:
        debug_dir.mkdir(parents=True, exist_ok=True)
        print(f"Headless mode: {debug_dir}")

    print("\nRunning... Press Ctrl+C to stop.\n")
    started_at = time.time()

    try:
        while True:
            if args.max_time > 0 and time.time() - started_at >= args.max_time:
                print(f"MAX_TIME reached: {args.max_time:.1f}s", flush=True)
                break

            frame = camera.read()
            frame_count += 1

            # Detect faces on GPU
            faces = detector.detect(frame)

            if frame_count % 5 == 0:
                print(f"frame={frame_count} faces={len(faces)}", flush=True)

            for face in faces:
                x1, y1, x2, y2 = face["bbox"]
                aligned_face = face_align.norm_crop(frame, landmark=face["kps"], image_size=112)

                # Get embedding on GPU
                embedding = recognizer.get_embedding(aligned_face)
                sim = cosine_similarity(avg_embedding, embedding)

                if sim >= args.threshold:
                    name = person_name
                    color = (0, 255, 0)
                else:
                    name = "Unknown"
                    color = (0, 0, 255)

                print(f"  face: sim={sim:.3f} name={name}", flush=True)

                # Draw
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(frame, f"{name} ({sim:.2f})", (x1, y1 - 10),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

                # TTS — once only
                if sim >= args.threshold and not greeted:
                    greeted = True
                    if not args.no_tts:
                        speak_text(f"Привет, {person_name}, вот ваш кофе!",
                                  tts_url=args.tts_url, volume=args.tts_volume,
                                  amplification_db=args.tts_amplification_db,
                                  timeout_s=args.tts_timeout)
                    print(f"GREETED: {person_name} (sim={sim:.3f}). Exiting to reduce load.", flush=True)
                    break

            if greeted:
                break

            # HUD
            cv2.putText(frame, f"frame={frame_count} faces={len(faces)} GPU=TensorRT",
                       (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)

            if headless:
                cv2.imwrite(str(debug_dir / "latest.jpg"), frame)
            else:
                cv2.imshow("Face Recognition — TensorRT GPU", frame)
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
