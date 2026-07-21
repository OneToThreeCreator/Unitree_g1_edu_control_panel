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

## [2026-07-09] User Requirements

**Developer:** User (darknight)

**Requirement:**
- При тестировании детекции и видения робота — выводить все **визуально в отдельном окне** (OpenCV window), не только логами.
- Пользователь хочет видеть bbox, классы, координаты, дистанции прямо на видео в реальном времени.

**Next:**
- Ожидается файл от пользователя для работы с TTS моделью.

---

## [2026-07-09] Voice Conversation Feature (from Codex session)

**Developer:** Codex (previous AI assistant) — reviewed by MiMoCode

**Status:** COMPLETE and TESTED on robot

**What was done:**
- Added `ConversationConfig` class and voice functions to `itog_v2.py`
- Installed Ollama Cloud authorization on robot
- Created `.venv_voice` with `ollama`, `SpeechRecognition`, `sounddevice`
- USB microphone `AB13X USB Audio` (device `0`, 48000 Hz) tested and working
- TTS endpoint verified: `http://192.168.1.102/api/audio/tts`

**Voice algorithm:**
1. Robot waits for wake word: **"Кузьмич"**
2. Says: **"слушаю тебя"**
3. Listens for speech up to 10 seconds
4. Converts voice to text via Google STT
5. Sends text to `gemma4:31b-cloud` via Ollama
6. Ollama responds → robot speaks via TTS
7. If no speech after wake word → proceeds to cup search

**Robot files:**
- `/home/unitree/yolo_cup_project/V2/itog_v2.py` (updated, 74KB)
- `/home/unitree/yolo_cup_project/V2/requirements-voice.txt`
- `/home/unitree/yolo_cup_project/V2/run_voice_robot.sh`
- `/home/unitree/yolo_cup_project/V2/.venv_voice/`

**V3 status:**
- Copied `itog_v3.py` (with voice block) to V3
- Created `run_voice_robot.sh` for V3
- `requirements-voice.txt` copied

**Local V3 status:**
- Added voice block (`ConversationConfig`, `run_conversation_gate`, etc.) to local `itog_v3.py`
- Added CLI arguments for voice mode
- Created `requirements-voice.txt` and `run_voice_robot.sh`
- Syntax verified: `py_compile` passes

**Launch on robot:**
```bash
cd /home/unitree/yolo_cup_project/V3
./run_voice_robot.sh
```
Or manually:
```bash
.venv_voice/bin/python itog_v3.py eth0 --real --conversation-input-device 0
```

---

## [2026-07-09] Additional Reports Loaded

**Developer:** User (darknight)

**Files loaded:**
- `VOICE_CONVERSATION_REPORT.md` — detailed voice conversation report
- `WORK_REPORT_FOR_NEXT_CHAT2.md` — updated full project report

**Key info from reports:**

**Connection:**
- SSH: `unitree@192.168.1.102` / password: `123`
- At `ros:foxy(1) noetic(2) ?` prompt → select `1`

**Robot hardware:**
- Hands: Inspire RH56E2 tactile (Modbus TCP)
  - Left: 192.168.123.210:6000
  - Right: 192.168.123.211:6000
- Arm player: UDP 127.0.0.1:15001
- Camera: RealSense (color + depth aligned)
- Microphone: AB13X USB Audio (device 0, 48000 Hz)

**Known issues:**
- Right thumb (channel 5) not working — hardware fault (CURRENT=0)
- Ollama Cloud may need re-auth after robot reboot

**TTS settings:**
- Endpoint: http://192.168.1.102/api/audio/tts
- Volume: 85
- Amplification: 13 dB
- Page: http://192.168.1.102/audio.html

---

## [2026-07-09] gemma4:e2b Model Download Started

**Developer:** MiMoCode (AI Assistant)

**Actions:**
- Started downloading `gemma4:e2b` (7.2 GB local model) on robot
- Background PID: 6475
- Log: `/home/unitree/pull_gemma4_e2b.log`
- Estimated time: ~1h 15min at 1.5 MB/s

**Model details:**
- `gemma4:e2b` — local model (NOT cloud)
- Size: 7.2 GB
- Will run on robot's GPU (Tegra)
- After download: update `--conversation-model gemma4:e2b` in launch command

**Next steps after download:**
1. Verify model with `ollama run gemma4:e2b "test"`
2. Update `run_voice_robot.sh` or CLI args to use `gemma4:e2b`
3. Test voice conversation with new model

---

## [2026-07-09] gemma4:e2b Download Complete

**Developer:** MiMoCode (AI Assistant)

**Status:** COMPLETE

**Actions:**
- Download completed (~20 minutes total)
- Model verified: `ollama run gemma4:e2b "тест"` → "Да" (22.23 tokens/s)
- Updated `itog_v3.py` (local + robot) to use `gemma4:e2b` by default
- Syntax verified: `py_compile` passes

**Robot models now available:**
```
gemma4:e2b          7.2 GB   (local, GPU)
gemma4:31b-cloud    -        (cloud, requires auth)
qwen2.5:3b          1.9 GB   (local, GPU)
```

**Launch with new model:**
```bash
cd /home/unitree/yolo_cup_project/V3
./run_voice_robot.sh
```
Or manually:
```bash
.venv_voice/bin/python itog_v3.py eth0 --real --conversation-input-device 0 --conversation-model gemma4:e2b
```

---
