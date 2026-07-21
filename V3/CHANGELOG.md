# Unitree G1 Edu Ultimate D — Development Log

This file tracks all changes made by different developers to avoid confusion.

---

## [2026-07-08] Session Start

**Developer:** MiMoCode (AI Assistant)

**Actions:**
- Created project directory: `unitree-g1-edu-ultimate-d/`
- Created this CHANGELOG.md file to track development progress

---

## [2026-07-09] Project Files Loaded

**Developer:** User (darknight)

**Actions:**
- Uploaded `WORK_REPORT_FOR_NEXT_CHAT.md` — полный отчет по проделанной работе
- Uploaded `V2.zip` (76 MB) — все наработки проекта
- Распакованы файлы в папку `V2/`

**Key Files:**
- `itog_v2.py` — основной скрипт миссии (доставка кофе)
- `see_for_robot_v2.py` — live-стрим с YOLO
- `g1_arm_keyframe_player.cpp` — C++ bridge для управления руками
- `yolo11m.pt`, `yolo11m_960.engine` — модели YOLO/TensorRT

**Project Summary (from README):**
- Робот Unitree G1 Edu Ultimate D
- Схема: RealSense камера + YOLO + TensorRT
- Управление: UDP receiver + TTS голос + Modbus TCP для кистей Inspire E2
- Задача: поиск кружки кофе, движение к ней, хват руками

---

## [2026-07-09] YOLOv11n Model Downloaded & TensorRT Engine Built

**Developer:** MiMoCode (AI Assistant)

**Actions:**
- Downloaded `yolo11n.pt` (5.4 MB, 2.6M params) on robot at `/home/unitree/yolo_cup_project/V2/`
- Exported to ONNX: `yolo11n_640.onnx`, `yolo11n_960.onnx`
- Built TensorRT FP16 engine: `yolo11n_640.engine` (7.4 MB)
- Built TensorRT FP16 engine: `yolo11n_960.engine` (8.0 MB)

**Performance (yolo11n_640.engine):**
- Throughput: **205 FPS**
- GPU latency: **4.77 ms** (mean)
- Host latency: **5.36 ms** (mean)

**Performance (yolo11n_960.engine):**
- Throughput: **104 FPS**
- GPU latency: **9.53 ms** (mean)
- Host latency: ~10.7 ms (mean)

**Comparison with yolo11m_960.engine:**
- Model: 2.6M vs 20M params (7.7x smaller)
- Engine: 7.4 MB vs 42 MB (5.7x smaller)
- Classes: same 80 COCO classes (including "person"=0, "cup"=41)

**Robot files created/modified:**
- `/home/unitree/yolo_cup_project/V2/yolo11n.pt`
- `/home/unitree/yolo_cup_project/V2/yolo11n_640.onnx`
- `/home/unitree/yolo_cup_project/V2/yolo11n_960.onnx`
- `/home/unitree/yolo_cup_project/V2/yolo11n_640.engine`
- `/home/unitree/yolo_cup_project/V2/yolo11n_960.engine`

---

## [2026-07-10] Face Recognition TensorRT Backend Fixed

**Developer:** Codex

**Problem:**
- `insightface` ran through CPU-only `onnxruntime` on Jetson.
- `.venv_face` providers: `AzureExecutionProvider`, `CPUExecutionProvider`.
- This overheated the robot during continuous face recognition.

**Actions:**
- Fixed `face_recognize_trt.py` TensorRT buffer management.
- Added proper CUDA context cleanup.
- Added SCRFD output decode for bbox + landmarks.
- Added ArcFace alignment via `face_align.norm_crop`.
- Added `--self-test` and `--max-time`.
- Switched V3 face gate default backend to `trt`.
- Kept CPU backend available with `--face-backend cpu`.

**Verification:**
```text
SELF_TEST_OK faces=0 emb_shape=(512,) elapsed_ms=67.2
RealSense TRT test: ~130 frames / 5 seconds, clean exit.
```

---

## [2026-07-10] Face Recognition Gate Integrated Into V3

**Developer:** Codex

**Actions:**
- Pulled `face_recognize.py`, `face_recognize_trt.py`, `requirements-face.txt`, and `oleg_embeddings.npz` from robot V3 to local V3.
- Added `itog_v3_core/v3_face.py` as a subprocess wrapper around `face_recognize.py`.
- Added `--face-*` CLI arguments to `itog_v3.py`.
- Main mission can now run face recognition before YOLO cup search, then release RealSense and continue normal approach.
- Updated `FACE_RECOGNITION_REPORT.md` with integrated V3 launch commands.

**Primary launch example:**
```bash
cd /home/unitree/yolo_cup_project/V3
python3 itog_v3.py eth0 --real --motion-backend udp \
  --face-recognition --face-threshold 0.35 --face-timeout 30 \
  --model /home/unitree/yolo_cup_project/V3/yolo11n_960.engine --img-size 960
```

**Verification:**
- Local `py_compile` passed.
- Robot `py_compile` passed.
- Robot `.venv_face/bin/python face_recognize.py --help` works.

---

## [2026-07-09] V3 Directory Created

**Developer:** MiMoCode (AI Assistant)

**Actions:**
- Created `V3/` directory on robot: `/home/unitree/yolo_cup_project/V3/`
- Created local `V3/` directory
- Copied YOLOv11n models: `yolo11n.pt`, `yolo11n_640.engine`, `yolo11n_960.engine`
- Copied scripts from V2: `itog_v3.py`, `see_for_robot_v3.py`, `g1_arm_keyframe_player`, `arm_player.cfg`

**Robot V3 structure:**
```
/home/unitree/yolo_cup_project/V3/
├── itog_v3.py              # main mission script (to be modified for multi-class)
├── see_for_robot_v3.py     # live stream viewer
├── g1_arm_keyframe_player  # arm control binary
├── arm_player.cfg          # arm config
├── yolo11n.pt              # YOLOv11n weights
├── yolo11n_640.engine      # TensorRT 640 (205 FPS)
├── yolo11n_960.engine      # TensorRT 960 (104 FPS)
└── yolo11n_*.onnx          # ONNX exports
```

---
