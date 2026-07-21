"""RealSense color+depth camera and YOLO/TensorRT cup detector."""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from v3_common import COCO_NAMES, DEFAULT_DEBUG_DIR, DEFAULT_MODEL_PATH, _require_cv2_numpy, clamp, cv2, np

@dataclass(frozen=True)
class VisionConfig:
    model_path: Path = DEFAULT_MODEL_PATH
    target_class: str = "cup"
    img_size: int = 960
    conf: float = 0.05
    device: str = "cuda:0"
    require_gpu: bool = True
    color_width: int = 1280
    color_height: int = 720
    color_fps: int = 30
    depth_width: int = 640
    depth_height: int = 480
    depth_fps: int = 30
    disable_ir_emitter: bool = False
    min_depth_m: float = 0.20
    max_depth_m: float = 4.00
    depth_roi_ratio: float = 0.45
    save_debug: bool = False
    debug_dir: Path = DEFAULT_DEBUG_DIR
    profile_every: int = 0


@dataclass
class Detection3D:
    bbox: list[float]
    confidence: float
    class_name: str
    X_m: float
    Y_m: float
    Z_m: float
    distance_m: float
    center_px: tuple[float, float]
    depth_samples: int
    yolo_dt_s: float
    frame_id: int


class RealSenseColorDepthCamera:
    def __init__(self, config: VisionConfig):
        _require_cv2_numpy()
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
            self._disable_ir_emitter(depth_sensor)

        color_stream = profile.get_stream(self.rs.stream.color).as_video_stream_profile()
        self.color_intrinsics = color_stream.get_intrinsics()
        logging.info(
            "RealSense color+depth started: color=%dx%d@%d depth=%dx%d@%d depth_scale=%.6f",
            self.config.color_width,
            self.config.color_height,
            self.config.color_fps,
            self.config.depth_width,
            self.config.depth_height,
            self.config.depth_fps,
            self.depth_scale,
        )
        logging.info(
            "Color intrinsics: fx=%.2f fy=%.2f cx=%.2f cy=%.2f",
            self.color_intrinsics.fx,
            self.color_intrinsics.fy,
            self.color_intrinsics.ppx,
            self.color_intrinsics.ppy,
        )

    def _disable_ir_emitter(self, depth_sensor: Any) -> None:
        for option_name, value in (("emitter_enabled", 0), ("laser_power", 0)):
            option = getattr(self.rs.option, option_name, None)
            if option is None:
                continue
            try:
                if depth_sensor.supports(option):
                    depth_sensor.set_option(option, value)
                    logging.info("RealSense %s set to %s", option_name, value)
            except Exception as exc:
                logging.warning("Cannot set RealSense %s=%s: %s", option_name, value, exc)

    def read(self) -> tuple[np.ndarray, np.ndarray, int]:
        frames = self.pipeline.wait_for_frames()
        aligned = self.align.process(frames)
        color_frame = aligned.get_color_frame()
        depth_frame = aligned.get_depth_frame()
        if not color_frame or not depth_frame:
            raise RuntimeError("Не удалось получить color/depth кадры RealSense")

        color = np.asanyarray(color_frame.get_data())
        depth = np.asanyarray(depth_frame.get_data())
        self.frame_id += 1
        return color, depth, self.frame_id

    def release(self) -> None:
        self.pipeline.stop()


class TensorRTYoloEngine:
    def __init__(self, engine_path: Path, img_size: int, conf: float, target_class: str):
        _require_cv2_numpy()
        import tensorrt as trt
        from cuda import cudart

        self.trt = trt
        self.cudart = cudart
        self.img_size = int(img_size)
        self.conf = float(conf)
        self.class_id = self._class_id(target_class)
        self.class_name = COCO_NAMES[self.class_id]
        self.logger = trt.Logger(trt.Logger.WARNING)

        with engine_path.open("rb") as f, trt.Runtime(self.logger) as runtime:
            self.engine = runtime.deserialize_cuda_engine(f.read())
        if self.engine is None:
            raise RuntimeError(f"Не удалось загрузить TensorRT engine: {engine_path}")

        self.context = self.engine.create_execution_context()
        self.input_idx = 0
        self.output_idx = 1
        self.input_shape = tuple(self.engine.get_binding_shape(self.input_idx))
        self.output_shape = tuple(self.engine.get_binding_shape(self.output_idx))
        if self.input_shape != (1, 3, self.img_size, self.img_size):
            raise RuntimeError(f"Engine input shape {self.input_shape} не совпадает с img_size={self.img_size}")
        if len(self.output_shape) != 3 or self.output_shape[1] < 5:
            raise RuntimeError(f"Неожиданный YOLO output shape: {self.output_shape}")

        self.input_host = np.empty(self.input_shape, dtype=np.float32)
        self.output_host = np.empty(self.output_shape, dtype=np.float32)
        self.input_nbytes = int(self.input_host.nbytes)
        self.output_nbytes = int(self.output_host.nbytes)
        self.stream = self._check(cudart.cudaStreamCreate())[1]
        self.input_dev = self._check(cudart.cudaMalloc(self.input_nbytes))[1]
        self.output_dev = self._check(cudart.cudaMalloc(self.output_nbytes))[1]
        self.bindings = [int(self.input_dev), int(self.output_dev)]
        logging.info("TensorRT direct backend ready: input=%s output=%s", self.input_shape, self.output_shape)

    def _check(self, result: tuple[Any, ...]) -> tuple[Any, ...]:
        err = result[0]
        if err != self.cudart.cudaError_t.cudaSuccess:
            raise RuntimeError(f"CUDA error: {err}")
        return result

    @staticmethod
    def _class_id(name: str) -> int:
        wanted = name.lower()
        for idx, class_name in COCO_NAMES.items():
            if class_name.lower() == wanted:
                return idx
        raise ValueError(f"Класс {name!r} не найден в COCO_NAMES")

    def predict_best(self, frame: np.ndarray) -> Optional[tuple[list[float], float, str]]:
        original_h, original_w = frame.shape[:2]
        resized, scale, pad_x, pad_y = self._letterbox(frame)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        chw = rgb.transpose(2, 0, 1).astype(np.float32) / 255.0
        self.input_host[0] = chw

        self._check(
            self.cudart.cudaMemcpyAsync(
                self.input_dev,
                self.input_host.ctypes.data,
                self.input_nbytes,
                self.cudart.cudaMemcpyKind.cudaMemcpyHostToDevice,
                self.stream,
            )
        )
        ok = self.context.execute_async_v2(self.bindings, self.stream)
        if not ok:
            raise RuntimeError("TensorRT execute_async_v2 вернул False")
        self._check(
            self.cudart.cudaMemcpyAsync(
                self.output_host.ctypes.data,
                self.output_dev,
                self.output_nbytes,
                self.cudart.cudaMemcpyKind.cudaMemcpyDeviceToHost,
                self.stream,
            )
        )
        self._check(self.cudart.cudaStreamSynchronize(self.stream))
        return self._postprocess(original_w, original_h, scale, pad_x, pad_y)

    def _letterbox(self, frame: np.ndarray) -> tuple[np.ndarray, float, float, float]:
        h, w = frame.shape[:2]
        scale = min(self.img_size / w, self.img_size / h)
        new_w = int(round(w * scale))
        new_h = int(round(h * scale))
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((self.img_size, self.img_size, 3), 114, dtype=np.uint8)
        pad_x = (self.img_size - new_w) / 2.0
        pad_y = (self.img_size - new_h) / 2.0
        x0 = int(round(pad_x - 0.1))
        y0 = int(round(pad_y - 0.1))
        canvas[y0 : y0 + new_h, x0 : x0 + new_w] = resized
        return canvas, scale, float(x0), float(y0)

    def _postprocess(
        self,
        original_w: int,
        original_h: int,
        scale: float,
        pad_x: float,
        pad_y: float,
    ) -> Optional[tuple[list[float], float, str]]:
        pred = self.output_host[0]
        scores = pred[4 + self.class_id]
        keep = scores >= self.conf
        if not np.any(keep):
            return None

        xywh = pred[:4, keep].T
        confs = scores[keep].astype(float)
        boxes_xyxy = np.empty_like(xywh)
        boxes_xyxy[:, 0] = xywh[:, 0] - xywh[:, 2] * 0.5
        boxes_xyxy[:, 1] = xywh[:, 1] - xywh[:, 3] * 0.5
        boxes_xyxy[:, 2] = xywh[:, 0] + xywh[:, 2] * 0.5
        boxes_xyxy[:, 3] = xywh[:, 1] + xywh[:, 3] * 0.5

        boxes_xyxy[:, [0, 2]] = (boxes_xyxy[:, [0, 2]] - pad_x) / scale
        boxes_xyxy[:, [1, 3]] = (boxes_xyxy[:, [1, 3]] - pad_y) / scale
        boxes_xyxy[:, [0, 2]] = np.clip(boxes_xyxy[:, [0, 2]], 0, original_w - 1)
        boxes_xyxy[:, [1, 3]] = np.clip(boxes_xyxy[:, [1, 3]], 0, original_h - 1)

        nms_boxes = []
        for x1, y1, x2, y2 in boxes_xyxy:
            nms_boxes.append([float(x1), float(y1), float(max(0.0, x2 - x1)), float(max(0.0, y2 - y1))])
        indices = cv2.dnn.NMSBoxes(nms_boxes, confs.tolist(), self.conf, 0.45)
        if len(indices) == 0:
            return None
        best_i = int(np.array(indices).reshape(-1)[0])
        bbox = [float(v) for v in boxes_xyxy[best_i].tolist()]
        return bbox, float(confs[best_i]), self.class_name

    def release(self) -> None:
        for ptr in (getattr(self, "input_dev", None), getattr(self, "output_dev", None)):
            if ptr:
                self.cudart.cudaFree(ptr)
        if getattr(self, "stream", None):
            self.cudart.cudaStreamDestroy(self.stream)


class YoloDepthCupDetector:
    def __init__(self, config: VisionConfig):
        _require_cv2_numpy()

        self.config = config
        self.camera = RealSenseColorDepthCamera(config)
        model_path = config.model_path.expanduser().resolve()
        self.is_engine = model_path.suffix.lower() == ".engine"
        if not model_path.exists():
            raise FileNotFoundError(f"Model not found: {model_path}")

        self._check_acceleration_if_required(config, model_path)
        if self.is_engine:
            self.model = TensorRTYoloEngine(model_path, config.img_size, config.conf, config.target_class)
        else:
            from ultralytics import YOLO

            self.model = YOLO(str(model_path))
        self.camera.start()
        self._detect_count = 0

        if model_path.suffix.lower() != ".engine":
            logging.warning(
                "Model is not TensorRT engine: %s. V2 works best with .engine on GPU.",
                model_path.name,
            )

    def _check_acceleration_if_required(self, config: VisionConfig, model_path: Path) -> None:
        if not config.require_gpu:
            return
        if model_path.suffix.lower() == ".engine":
            try:
                import tensorrt as trt
            except ImportError as exc:
                raise RuntimeError(
                    "--require-gpu задан и модель .engine, но Python tensorrt не импортируется. "
                    "Проверь TensorRT на роботе."
                ) from exc
            logging.info("TensorRT available: %s", getattr(trt, "__version__", "unknown"))
            return

        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("--require-gpu задан, но torch не установлен") from exc
        if not torch.cuda.is_available():
            raise RuntimeError(
                "--require-gpu задан и модель не .engine, но torch.cuda.is_available() == False. "
                "Для .pt нужна CUDA-сборка PyTorch; для TensorRT используй .engine."
            )

    @staticmethod
    def _bbox_center(bbox: Sequence[float]) -> tuple[float, float]:
        x1, y1, x2, y2 = bbox
        return (float(x1) + float(x2)) * 0.5, (float(y1) + float(y2)) * 0.5

    def _best_bbox(self, frame: np.ndarray) -> Optional[tuple[list[float], float, str]]:
        if self.is_engine:
            return self.model.predict_best(frame)

        predict_kwargs: dict[str, Any] = {
            "source": frame,
            "imgsz": self.config.img_size,
            "conf": self.config.conf,
            "verbose": False,
        }
        if self.config.device:
            if self.is_engine:
                logging.debug("Ignoring --device for TensorRT engine inference.")
            else:
                predict_kwargs["device"] = self.config.device

        results = self.model.predict(**predict_kwargs)
        best_bbox = None
        best_conf = -1.0
        best_name = ""

        for box in results[0].boxes:
            cls_id = int(box.cls[0])
            class_name = self.model.names[cls_id]
            if class_name.lower() != self.config.target_class.lower():
                continue
            confidence = float(box.conf[0])
            if confidence <= best_conf:
                continue
            best_conf = confidence
            best_name = class_name
            best_bbox = [float(v) for v in box.xyxy[0].tolist()]

        if best_bbox is None:
            return None
        return best_bbox, best_conf, best_name

    def _median_depth_in_bbox(self, depth: np.ndarray, bbox: Sequence[float]) -> tuple[float, int]:
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
            raise ValueError("Нет валидной depth внутри bbox")

        return float(np.median(valid)), int(valid.size)

    def detect(self) -> Optional[Detection3D]:
        loop_t0 = time.monotonic()
        self._detect_count += 1
        should_profile = self.config.profile_every > 0 and self._detect_count % self.config.profile_every == 0

        camera_t0 = time.monotonic()
        color, depth, frame_id = self.camera.read()
        camera_dt = time.monotonic() - camera_t0

        t0 = time.monotonic()
        best = self._best_bbox(color)
        yolo_dt = time.monotonic() - t0
        if best is None:
            debug_t0 = time.monotonic()
            self._save_debug(color, None, None, yolo_dt, frame_id)
            debug_dt = time.monotonic() - debug_t0
            if should_profile:
                total_dt = time.monotonic() - loop_t0
                logging.info(
                    "PROFILE detect frame=%d found=0 camera_align=%.1fms yolo=%.1fms depth_roi=0.0ms debug=%.1fms total=%.1fms",
                    frame_id,
                    camera_dt * 1000.0,
                    yolo_dt * 1000.0,
                    debug_dt * 1000.0,
                    total_dt * 1000.0,
                )
            return None

        bbox, conf, class_name = best
        depth_t0 = time.monotonic()
        z_m, samples = self._median_depth_in_bbox(depth, bbox)
        depth_dt = time.monotonic() - depth_t0
        u, v = self._bbox_center(bbox)
        intr = self.camera.color_intrinsics
        if intr is None:
            raise RuntimeError("RealSense color intrinsics не инициализированы")

        x_m = (u - intr.ppx) * z_m / intr.fx
        y_m = (v - intr.ppy) * z_m / intr.fy
        det = Detection3D(
            bbox=bbox,
            confidence=conf,
            class_name=class_name,
            X_m=x_m,
            Y_m=y_m,
            Z_m=z_m,
            distance_m=math.sqrt(x_m * x_m + y_m * y_m + z_m * z_m),
            center_px=(u, v),
            depth_samples=samples,
            yolo_dt_s=yolo_dt,
            frame_id=frame_id,
        )
        debug_t0 = time.monotonic()
        self._save_debug(color, bbox, det, yolo_dt, frame_id)
        debug_dt = time.monotonic() - debug_t0
        if should_profile:
            total_dt = time.monotonic() - loop_t0
            logging.info(
                "PROFILE detect frame=%d found=1 camera_align=%.1fms yolo=%.1fms depth_roi=%.1fms debug=%.1fms total=%.1fms conf=%.2f X=%.3f Z=%.3f",
                frame_id,
                camera_dt * 1000.0,
                yolo_dt * 1000.0,
                depth_dt * 1000.0,
                debug_dt * 1000.0,
                total_dt * 1000.0,
                conf,
                det.X_m,
                det.Z_m,
            )
        return det

    def _save_debug(
        self,
        color: np.ndarray,
        bbox: Optional[Sequence[float]],
        det: Optional[Detection3D],
        yolo_dt: float,
        frame_id: int,
    ) -> None:
        if not self.config.save_debug:
            return

        debug_dir = self.config.debug_dir.expanduser().resolve()
        debug_dir.mkdir(parents=True, exist_ok=True)
        img = color.copy()

        if bbox is None:
            cv2.putText(img, "cup not found", (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 255), 2)
        else:
            x1, y1, x2, y2 = [int(v) for v in bbox]
            u, v = self._bbox_center(bbox)
            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.circle(img, (int(u), int(v)), 5, (0, 0, 255), -1)
            if det is not None:
                text = (
                    f"conf={det.confidence:.2f} X={det.X_m:+.2f}m "
                    f"Z={det.Z_m:.2f}m yolo={yolo_dt:.2f}s"
                )
                cv2.putText(img, text, (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)

        cv2.putText(img, f"frame={frame_id}", (20, img.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 0), 2)
        cv2.imwrite(str(debug_dir / "latest_color_depth_yolo.jpg"), img)

    def release(self) -> None:
        if self.is_engine and hasattr(self.model, "release"):
            self.model.release()
        self.camera.release()
