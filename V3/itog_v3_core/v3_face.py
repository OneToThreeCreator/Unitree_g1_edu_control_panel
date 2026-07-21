"""Optional face-recognition gate for the V3 mission.

The insightface stack is kept in a separate process because it has heavy
dependencies and owns the RealSense camera while it runs. The subprocess exits
after greeting a known person, then the normal cup pipeline can open the camera.
"""
from __future__ import annotations

import logging
import math
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from v3_common import SCRIPT_DIR, clamp, emit_event, np
from v3_vision import Detection3D, RealSenseColorDepthCamera, VisionConfig


@dataclass
class FaceRecognitionConfig:
    enabled: bool = False
    required: bool = False
    backend: str = "trt"
    python_path: Optional[str] = None
    script_path: Path = SCRIPT_DIR / "face_recognize.py"
    trt_script_path: Path = SCRIPT_DIR / "face_recognize_trt.py"
    embeddings_path: Path = SCRIPT_DIR / "oleg_embeddings.npz"
    threshold: float = 0.35
    timeout_s: float = 30.0
    det_size: int = 640
    debug_dir: Path = Path("/tmp/face_debug")
    headless: bool = True
    no_tts: bool = False


class OlegDepthFaceDetector:
    """Real-time Oleg detector with RealSense depth for follow/approach mode."""

    def __init__(
        self,
        config: VisionConfig,
        embeddings_path: Path,
        threshold: float = 0.35,
        det_engine: Optional[Path] = None,
        rec_engine: Optional[Path] = None,
        min_face_score: float = 0.50,
    ):
        from face_recognize_trt import (
            MODELS_DIR,
            FaceDetectorTRT,
            FaceRecognizerTRT,
            cleanup_cuda_context,
            cosine_similarity,
        )
        from insightface.utils import face_align

        self.config = config
        self.camera = RealSenseColorDepthCamera(config)
        self.threshold = float(threshold)
        self.min_face_score = float(min_face_score)
        self.face_align = face_align
        self.cosine_similarity = cosine_similarity
        self.cleanup_cuda_context = cleanup_cuda_context

        emb_path = embeddings_path.expanduser().resolve()
        data = np.load(str(emb_path), allow_pickle=True)
        self.person_name = str(data["name"])
        self.avg_embedding = data["avg_embedding"].astype(np.float32)
        self.avg_embedding = self.avg_embedding / np.linalg.norm(self.avg_embedding)

        models_dir = Path(MODELS_DIR).expanduser()
        det_path = (det_engine or (models_dir / "det_10g.engine")).expanduser().resolve()
        rec_path = (rec_engine or (models_dir / "w600k_r50.engine")).expanduser().resolve()
        if not det_path.exists():
            raise FileNotFoundError(f"Oleg face detection engine not found: {det_path}")
        if not rec_path.exists():
            raise FileNotFoundError(f"Oleg face recognition engine not found: {rec_path}")

        self.face_detector = FaceDetectorTRT(str(det_path), conf_thresh=self.min_face_score)
        self.face_recognizer = FaceRecognizerTRT(str(rec_path))
        self.camera.start()
        self._detect_count = 0
        emit_event(
            f"OLEG detector ready: name={self.person_name} threshold={self.threshold:.2f} "
            f"det={det_path.name} rec={rec_path.name}"
        )

    @staticmethod
    def _bbox_center(bbox: Sequence[float]) -> tuple[float, float]:
        x1, y1, x2, y2 = bbox
        return (float(x1) + float(x2)) * 0.5, (float(y1) + float(y2)) * 0.5

    def _median_depth_in_bbox(self, depth, bbox: Sequence[float]) -> tuple[float, int]:
        h, w = depth.shape[:2]
        x1, y1, x2, y2 = bbox
        cx, cy = self._bbox_center(bbox)

        roi_w = max(4.0, (x2 - x1) * self.config.depth_roi_ratio)
        roi_h = max(4.0, (y2 - y1) * self.config.depth_roi_ratio)
        rx1 = int(clamp(cx - roi_w * 0.5, 0, w - 1))
        rx2 = int(clamp(cx + roi_w * 0.5, 0, w))
        ry1 = int(clamp(cy - roi_h * 0.5, 0, h - 1))
        ry2 = int(clamp(cy + roi_h * 0.5, 0, h))

        roi = depth[ry1:ry2, rx1:rx2].astype(np.float32) * self.camera.depth_scale
        valid = roi[(roi >= self.config.min_depth_m) & (roi <= self.config.max_depth_m)]
        if valid.size == 0:
            raise ValueError("Нет валидной depth внутри bbox лица")

        return float(np.median(valid)), int(valid.size)

    def detect(self) -> Optional[Detection3D]:
        loop_t0 = time.monotonic()
        self._detect_count += 1
        should_profile = self.config.profile_every > 0 and self._detect_count % self.config.profile_every == 0

        color, depth, frame_id = self.camera.read()
        faces = self.face_detector.detect(color)
        best_face = None
        best_sim = -1.0

        for face in faces:
            if float(face.get("score", 0.0)) < self.min_face_score:
                continue
            aligned_face = self.face_align.norm_crop(color, landmark=face["kps"], image_size=112)
            embedding = self.face_recognizer.get_embedding(aligned_face)
            sim = self.cosine_similarity(self.avg_embedding, embedding)
            if sim >= self.threshold and sim > best_sim:
                best_sim = sim
                best_face = face

        yolo_dt = time.monotonic() - loop_t0
        if best_face is None:
            if should_profile:
                logging.info("PROFILE oleg frame=%d found=0 faces=%d total=%.1fms", frame_id, len(faces), yolo_dt * 1000.0)
            return None

        bbox = [float(v) for v in best_face["bbox"]]
        z_m, samples = self._median_depth_in_bbox(depth, bbox)
        u, v = self._bbox_center(bbox)
        intr = self.camera.color_intrinsics
        if intr is None:
            raise RuntimeError("RealSense color intrinsics не инициализированы")

        x_m = (u - intr.ppx) * z_m / intr.fx
        y_m = (v - intr.ppy) * z_m / intr.fy
        det = Detection3D(
            bbox=bbox,
            confidence=float(best_sim),
            class_name=self.person_name,
            X_m=x_m,
            Y_m=y_m,
            Z_m=z_m,
            distance_m=math.sqrt(x_m * x_m + y_m * y_m + z_m * z_m),
            center_px=(u, v),
            depth_samples=samples,
            yolo_dt_s=yolo_dt,
            frame_id=frame_id,
        )
        if should_profile:
            logging.info(
                "PROFILE oleg frame=%d found=1 faces=%d sim=%.3f X=%.3f Y=%.3f Z=%.3f samples=%d total=%.1fms",
                frame_id,
                len(faces),
                best_sim,
                det.X_m,
                det.Y_m,
                det.Z_m,
                det.depth_samples,
                yolo_dt * 1000.0,
            )
        return det

    def release(self) -> None:
        try:
            self.camera.release()
        finally:
            try:
                self.cleanup_cuda_context()
            except Exception:
                logging.exception("Oleg CUDA context cleanup failed")


def _default_face_python() -> str:
    venv_python = SCRIPT_DIR / ".venv_face" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return sys.executable


def run_face_recognition_gate(
    config: FaceRecognitionConfig,
    *,
    tts_url: str,
    tts_volume: int,
    tts_amplification_db: float,
    tts_timeout_s: float,
) -> bool:
    if not config.enabled:
        return False

    if config.backend not in {"trt", "cpu"}:
        raise ValueError("Face backend must be 'trt' or 'cpu'")

    script_path = (
        config.trt_script_path if config.backend == "trt" else config.script_path
    ).expanduser().resolve()
    embeddings_path = config.embeddings_path.expanduser().resolve()
    if not script_path.exists():
        message = f"Face recognition script not found: {script_path}"
        if config.required:
            raise FileNotFoundError(message)
        logging.warning(message)
        return False
    if not embeddings_path.exists():
        message = f"Face embeddings not found: {embeddings_path}"
        if config.required:
            raise FileNotFoundError(message)
        logging.warning(message)
        return False

    python_path = config.python_path or _default_face_python()
    command = [
        python_path,
        str(script_path),
        "--embeddings",
        str(embeddings_path),
        "--threshold",
        str(config.threshold),
        "--debug-dir",
        str(config.debug_dir.expanduser()),
        "--tts-url",
        tts_url,
        "--tts-volume",
        str(tts_volume),
        "--tts-amplification-db",
        str(tts_amplification_db),
        "--tts-timeout",
        str(tts_timeout_s),
    ]
    if config.headless:
        command.append("--headless")
    if config.no_tts:
        command.append("--no-tts")
    if config.backend == "trt":
        command.extend(["--max-time", str(config.timeout_s)])
    else:
        command.extend(["--det-size", str(config.det_size)])

    emit_event(f"FACE gate start ({config.backend}): " + " ".join(command))
    try:
        process_timeout_s = max(1.0, config.timeout_s) + 15.0
        completed = subprocess.run(
            command,
            cwd=str(SCRIPT_DIR),
            timeout=process_timeout_s,
            text=True,
            capture_output=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        message = f"FACE gate process timeout after {config.timeout_s + 15.0:.1f}s."
        if config.required:
            raise RuntimeError(message)
        logging.warning(message)
        return False

    if completed.stdout:
        for line in completed.stdout.splitlines():
            emit_event(f"FACE stdout: {line}")
    if completed.stderr:
        for line in completed.stderr.splitlines():
            logging.warning("FACE stderr: %s", line)

    if completed.returncode != 0:
        message = f"FACE gate failed with code {completed.returncode}."
        if config.required:
            raise RuntimeError(message)
        logging.warning(message)
        return False

    recognized = "GREETED:" in completed.stdout
    emit_event(f"FACE gate done: recognized={recognized}")
    return recognized
