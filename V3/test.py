from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from ultralytics import YOLO


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_PATH = SCRIPT_DIR / "yolo11n_640.engine"
FALLBACK_MODEL_PATH = SCRIPT_DIR / "yolo11n.pt"
DEBUG_DIR = SCRIPT_DIR / "debug_frames"
CAMERA_SCAN_DIR = SCRIPT_DIR / "camera_scan"


def default_model_path() -> Path:
    if DEFAULT_MODEL_PATH.exists():
        return DEFAULT_MODEL_PATH
    return FALLBACK_MODEL_PATH


class DualCameraCupDetector:
    def __init__(
        self,
        model_path: Path,
        left_camera_id: int,
        right_camera_id: int,
        img_size: int,
        conf: float,
        target_class_name: str,
        save_every_n_frames: int,
        print_every_n_frames: int,
    ) -> None:
        self.model_path = model_path
        self.left_camera_id = left_camera_id
        self.right_camera_id = right_camera_id
        self.img_size = img_size
        self.conf = conf
        self.target_class_name = target_class_name.lower()
        self.save_every_n_frames = max(1, save_every_n_frames)
        self.print_every_n_frames = max(1, print_every_n_frames)
        self.model: YOLO | None = None

    def load_model(self) -> None:
        if not self.model_path.exists():
            raise FileNotFoundError(f"Модель не найдена: {self.model_path}")

        print("=" * 70)
        print("V3: ДВУХКАМЕРНЫЙ ТЕСТ ДЕТЕКЦИИ ЧАШКИ БЕЗ ОКНА")
        print("=" * 70)
        print(f"Модель: {self.model_path}")
        print(f"imgsz={self.img_size}, conf={self.conf}, target={self.target_class_name}")
        print(f"LEFT_CAMERA_ID={self.left_camera_id}, RIGHT_CAMERA_ID={self.right_camera_id}")

        self.model = YOLO(str(self.model_path))
        print("Доступные классы:")
        print(self.model.names)

        names = {str(v).lower() for v in self.model.names.values()}
        if self.target_class_name not in names:
            print()
            print(f"[WARNING] Класса '{self.target_class_name}' нет в модели.")
            print("Проверь --target-class или модель.")
            print()

    @staticmethod
    def get_bbox_center(bbox: list[int]) -> list[int]:
        x1, y1, x2, y2 = bbox
        return [(x1 + x2) // 2, (y1 + y2) // 2]

    def detect_target(self, frame: np.ndarray, camera_name: str) -> list[dict[str, Any]]:
        if self.model is None:
            raise RuntimeError("Модель не загружена")

        results = self.model.predict(
            source=frame,
            imgsz=self.img_size,
            conf=self.conf,
            verbose=False,
        )
        result = results[0]
        detections: list[dict[str, Any]] = []

        for box in result.boxes:
            cls_id = int(box.cls[0])
            class_name = str(self.model.names[cls_id])
            confidence = float(box.conf[0])

            if class_name.lower() != self.target_class_name:
                continue

            x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())
            bbox = [x1, y1, x2, y2]
            detections.append(
                {
                    "camera": camera_name,
                    "class_id": cls_id,
                    "class_name": class_name,
                    "confidence": confidence,
                    "bbox": bbox,
                    "center": self.get_bbox_center(bbox),
                }
            )

        detections.sort(key=lambda item: item["confidence"], reverse=True)
        return detections

    def draw_debug_frame(
        self,
        frame: np.ndarray,
        detections: list[dict[str, Any]],
        title: str,
    ) -> np.ndarray:
        img = frame.copy()
        if len(img.shape) == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

        cv2.putText(img, title, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 255), 2)

        if not detections:
            cv2.putText(img, "target not found", (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            return img

        for i, det in enumerate(detections):
            x1, y1, x2, y2 = det["bbox"]
            cx, cy = det["center"]
            conf = det["confidence"]

            cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.circle(img, (cx, cy), 5, (0, 0, 255), -1)
            cv2.putText(
                img,
                f"{det['class_name']} {conf:.2f}",
                (x1, max(y1 - 10, 20)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (0, 255, 0),
                2,
            )
            cv2.putText(
                img,
                f"bbox={det['bbox']}",
                (10, 70 + i * 25),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 0),
                2,
            )
            cv2.putText(
                img,
                f"center={det['center']}",
                (10, 95 + i * 25),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 0),
                2,
            )

        return img

    @staticmethod
    def resize_to_same_height(left_img: np.ndarray, right_img: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        h_left, w_left = left_img.shape[:2]
        h_right, w_right = right_img.shape[:2]
        target_h = min(h_left, h_right)

        if h_left != target_h:
            scale = target_h / h_left
            left_img = cv2.resize(left_img, (int(w_left * scale), target_h))

        if h_right != target_h:
            scale = target_h / h_right
            right_img = cv2.resize(right_img, (int(w_right * scale), target_h))

        return left_img, right_img

    def save_debug_images(
        self,
        frame_left: np.ndarray,
        frame_right: np.ndarray,
        left_detections: list[dict[str, Any]],
        right_detections: list[dict[str, Any]],
        frame_id: int,
    ) -> None:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)

        debug_left = self.draw_debug_frame(frame_left, left_detections, "LEFT CAMERA")
        debug_right = self.draw_debug_frame(frame_right, right_detections, "RIGHT CAMERA")
        debug_left, debug_right = self.resize_to_same_height(debug_left, debug_right)
        debug_combined = np.hstack((debug_left, debug_right))

        cv2.imwrite(str(DEBUG_DIR / "latest_raw_left.jpg"), frame_left)
        cv2.imwrite(str(DEBUG_DIR / "latest_raw_right.jpg"), frame_right)
        cv2.imwrite(str(DEBUG_DIR / "latest_left_with_bbox.jpg"), debug_left)
        cv2.imwrite(str(DEBUG_DIR / "latest_right_with_bbox.jpg"), debug_right)
        cv2.imwrite(str(DEBUG_DIR / "latest_combined.jpg"), debug_combined)

        if frame_id % 100 == 0:
            cv2.imwrite(str(DEBUG_DIR / f"frame_{frame_id:06d}_combined.jpg"), debug_combined)

    @staticmethod
    def open_camera(camera_id: int, camera_name: str) -> cv2.VideoCapture:
        cap = cv2.VideoCapture(camera_id, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise RuntimeError(f"Не удалось открыть {camera_name} камеру ID={camera_id}")
        print(f"{camera_name} камера ID={camera_id} запущена")
        return cap

    def run(self) -> None:
        self.load_model()
        cap_left = self.open_camera(self.left_camera_id, "Левая")
        cap_right = self.open_camera(self.right_camera_id, "Правая")

        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        print()
        print("Работа началась. Остановить: Ctrl+C")
        print(f"Фото сохраняются сюда: {DEBUG_DIR}")
        print(f"Главный файл для просмотра: {DEBUG_DIR / 'latest_combined.jpg'}")
        print("=" * 70)

        frame_id = 0
        try:
            while True:
                ret_left, frame_left = cap_left.read()
                ret_right, frame_right = cap_right.read()

                if not ret_left:
                    print("[ERROR] Не удалось получить кадр с левой камеры")
                    time.sleep(0.1)
                    continue

                if not ret_right:
                    print("[ERROR] Не удалось получить кадр с правой камеры")
                    time.sleep(0.1)
                    continue

                left_detections = self.detect_target(frame_left, "LEFT")
                right_detections = self.detect_target(frame_right, "RIGHT")
                best_left = left_detections[0] if left_detections else None
                best_right = right_detections[0] if right_detections else None

                if frame_id % self.print_every_n_frames == 0:
                    print()
                    print(f"[FRAME {frame_id}]")
                    self.print_best("LEFT", best_left)
                    self.print_best("RIGHT", best_right)

                    if best_left and best_right:
                        center_left = best_left["center"]
                        center_right = best_right["center"]
                        disparity_px = center_left[0] - center_right[0]
                        print("PAIR:")
                        print(f"  bbox_left     = {best_left['bbox']}")
                        print(f"  bbox_right    = {best_right['bbox']}")
                        print(f"  center_left   = {center_left}")
                        print(f"  center_right  = {center_right}")
                        print(f"  disparity_px  = {disparity_px}")
                    print("-" * 70)

                if frame_id % self.save_every_n_frames == 0:
                    self.save_debug_images(frame_left, frame_right, left_detections, right_detections, frame_id)
                    print(f"[DEBUG] Фото обновлены: {DEBUG_DIR / 'latest_combined.jpg'}")

                frame_id += 1

        except KeyboardInterrupt:
            print()
            print("Остановлено пользователем")
        finally:
            cap_left.release()
            cap_right.release()
            print("Камеры освобождены")

    @staticmethod
    def print_best(side: str, det: dict[str, Any] | None) -> None:
        print(f"{side}:")
        if not det:
            print("  target not found")
            return
        print(f"  class  = {det['class_name']}")
        print(f"  bbox   = {det['bbox']}")
        print(f"  center = {det['center']}")
        print(f"  conf   = {det['confidence']:.2f}")


def scan_camera_ids(scan_to: int) -> None:
    CAMERA_SCAN_DIR.mkdir(parents=True, exist_ok=True)
    found_ids: list[int] = []

    print("=" * 70)
    print("ПОИСК КАМЕР")
    print("=" * 70)
    print(f"Проверяю ID: 0..{scan_to}")
    print(f"Снимки будут сохранены сюда: {CAMERA_SCAN_DIR}")
    print()

    for camera_id in range(scan_to + 1):
        cap = cv2.VideoCapture(camera_id, cv2.CAP_V4L2)
        if not cap.isOpened():
            print(f"ID={camera_id}: не открылась")
            cap.release()
            continue

        frame = None
        ret = False
        for _ in range(8):
            ret, frame = cap.read()
            if ret and frame is not None:
                break
            time.sleep(0.05)
        cap.release()

        if not ret or frame is None:
            print(f"ID={camera_id}: открылась, но кадр не получен")
            continue

        found_ids.append(camera_id)
        height, width = frame.shape[:2]
        channels = 1 if len(frame.shape) == 2 else frame.shape[2]

        debug_frame = frame.copy()
        if len(debug_frame.shape) == 2:
            debug_frame = cv2.cvtColor(debug_frame, cv2.COLOR_GRAY2BGR)

        cv2.putText(debug_frame, f"CAMERA ID = {camera_id}", (20, 45), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)
        cv2.putText(debug_frame, f"{width}x{height}, channels={channels}", (20, 85), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 255), 2)

        out_path = CAMERA_SCAN_DIR / f"camera_id_{camera_id}.jpg"
        cv2.imwrite(str(out_path), debug_frame)
        print(f"ID={camera_id}: OK, {width}x{height}, channels={channels}, файл: {out_path}")

    print()
    print("=" * 70)
    if found_ids:
        print(f"Найдены камеры: {found_ids}")
        print("Открой JPG из camera_scan и выбери нужные ID для --left-camera-id/--right-camera-id.")
    else:
        print("Камеры не найдены. Проверь /dev/video*")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="V3 dual-camera YOLO test with debug JPG output.")
    parser.add_argument("--scan-cameras", action="store_true", help="Проверить ID камер и сохранить снимки.")
    parser.add_argument("--scan-to", type=int, default=15, help="Последний ID камеры для scan mode.")
    parser.add_argument("--model", type=Path, default=default_model_path(), help="Путь к .engine/.pt модели.")
    parser.add_argument("--left-camera-id", type=int, default=3)
    parser.add_argument("--right-camera-id", type=int, default=6)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--target-class", default="cup")
    parser.add_argument("--save-every", type=int, default=10)
    parser.add_argument("--print-every", type=int, default=1)
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.scan_cameras:
        scan_camera_ids(args.scan_to)
        return

    detector = DualCameraCupDetector(
        model_path=args.model.expanduser().resolve(),
        left_camera_id=args.left_camera_id,
        right_camera_id=args.right_camera_id,
        img_size=args.imgsz,
        conf=args.conf,
        target_class_name=args.target_class,
        save_every_n_frames=args.save_every,
        print_every_n_frames=args.print_every,
    )
    detector.run()


if __name__ == "__main__":
    main()
