# Отчет: Распознавание лиц (insightface) для робота Unitree G1

Дата: 2026-07-10

## Что сделано

Реализована система распознавания конкретного человека (Олег) через связку **insightface** (RetinaFace для детекции + ArcFace для распознавания). Работает на роботе через RealSense камеру, озвучивает приветствие через TTS.

## Архитектура

```
RealSense камера → insightface (CPU) → cosine similarity → TTS endpoint
                                         ↓
                                   "Привет, Олег, вот ваш кофе!"
                                         ↓
                                   скрипт завершается (экономия CPU)
```

## Файлы проекта

### На ноутбуке (регистрация):

| Файл | Описание |
|------|----------|
| `V3/face_register.py` | Регистрация лица — берёт фото, извлекает embeddings, сохраняет `.npz` |
| `V3/requirements-face.txt` | Зависимости (insightface, onnxruntime, opencv, numpy) |
| `V3/.venv_face/` | Виртуальное окружение с зависимостями |
| `V3/oleg_embeddings.npz` | Embeddings Олега (59 фото, 512-d векторы) |
| `V3/Oleg_photos/` | Исходные фото Олега (59 шт.) |

### На роботе:

| Файл | Описание |
|------|----------|
| `/home/unitree/yolo_cup_project/V3/face_recognize.py` | Основной скрипт распознавания |
| `/home/unitree/yolo_cup_project/V3/oleg_embeddings.npz` | Embeddings Олега |
| `/home/unitree/yolo_cup_project/V3/.venv_face/` | Виртуальное окружение |
| `/home/unitree/yolo_cup_project/V3/convert_to_trt.py` | Скрипт конвертации моделей в TensorRT (эксперимент) |

## Установка и запуск

### 1. Регистрация лица (на ноутбуке)

```bash
cd /home/saintofcoding/projects/unitree-g1-edu-ultimate-d/V3
python3 -m venv .venv_face
.venv_face/bin/pip install -r requirements-face.txt
.venv_face/bin/python face_register.py --input-dir Oleg_photos/ --output oleg_embeddings.npz --name Oleg
```

Результат:
- 59 фото обработано, 0 пропущено
- Confidence детекции: 0.75–0.91
- Embeddings: 512-d векторы, нормализованы

### 2. Установка на роботе

```bash
ssh unitree@192.168.1.102
cd /home/unitree/yolo_cup_project/V3
python3 -m venv --system-site-packages .venv_face
.venv_face/bin/pip install -r requirements-face.txt
.venv_face/bin/pip install "scipy>=1.7,<1.12"  # совместимость с numpy
```

### 3. Запуск распознавания

```bash
cd /home/unitree/yolo_cup_project/V3
.venv_face/bin/python face_recognize.py \
  --embeddings oleg_embeddings.npz \
  --headless \
  --debug-dir /tmp/face_debug \
  --threshold 0.35 \
  --tts-url http://192.168.1.102/api/audio/tts
```

### 4. Остановка

```bash
pkill -f face_recognize.py
```

## Параметры запуска

| Параметр | Значение | Описание |
|----------|----------|----------|
| `--threshold` | 0.35 | Порог cosine similarity для распознавания |
| `--headless` | вкл | Без OpenCV окна, сохраняет кадры в файл |
| `--debug-dir` | `/tmp/face_debug` | Папка для отладочных кадров |
| `--no-tts` | выкл | Отключить TTS (только визуал) |
| `--tts-url` | `http://192.168.1.102/api/audio/tts` | TTS endpoint |
| `--cooldown` | 10.0 | Пауза между приветствиями (сек) |

## Поведение системы

1. Робот запускает скрипт → RealSense камера начинает захват
2. Каждый кадр обрабатывается insightface (детекция + распознавание)
3. Если similarity ≥ порога (0.35) → робот говорит "Привет, Олег, вот ваш кофе!"
4. **Скрипт сразу завершается** после приветствия → экономия CPU, снижение нагрева
5. При следующем запуске снова скажет приветствие (один раз за сессию)

## Результаты тестирования

### Детекция и распознавание:

| Сценарий | Результат |
|----------|-----------|
| Фото на экране ноутбука (далеко) | faces=1, sim=0.27–0.32 (Unknown) |
| Фото на экране (ближе) | faces=1, sim=0.44 (почти Oleg) |
| Фото на уровне камеры (50 см) | faces=1, sim=0.54 (Oleg) |
| Фото прямо перед камерой | faces=1, sim=0.60–0.67 (Oleg) |

### TTS:

| Фраза | Результат |
|-------|-----------|
| "Привет, Oleg, вот ваш кофе!" | TTS_OK True, робот произнёс |

### Завершение после приветствия:

```
GREETED: Oleg (sim=0.536). Exiting to reduce CPU load.
Done.
```

Процессов face_recognize: **0** (скрипт завершился)

## Проблемы и решения

### 1. scipy несовместим с numpy

**Проблема:** Старый scipy (1.3.3) на роботе использует `np.typeDict`, удалённый в numpy 1.24.

**Решение:** Установить совместимую версию:
```bash
.venv_face/bin/pip install "scipy>=1.7,<1.12"
```

### 2. OpenCV без GUI (headless)

**Проблема:** На роботе нет GTK → `cv2.imshow()` падает с ошибкой.

**Решение:** Добавлен флаг `--headless` — сохраняет кадры в файл вместо окна.

### 3. GPU acceleration (TensorRT)

**Проблема:** `onnxruntime-gpu` не доступен для Python 3.8 + aarch64 (Jetson).

**Что сделано:**
- Сконвертированы все 5 моделей insightface в TensorRT engines (det_10g, w600k_r50, 2d106det, 1k3d68, genderage)
- Установлен pycuda
- Написан скрипт `face_recognize_trt.py` для прямого inference через TensorRT

**Решение 2026-07-10:** `face_recognize_trt.py` исправлен:
- GPU buffers теперь выделяются через `cuda.mem_alloc`, а не подменяются размером host-buffer.
- CUDA context создается один раз на процесс и корректно очищается через `atexit`.
- SCRFD outputs декодируются как `score/bbox/kps` для strides `8/16/32`.
- Перед ArcFace добавлено выравнивание лица по landmarks через `face_align.norm_crop`.
- Добавлены `--self-test` и `--max-time`.

**Проверено на роботе:**
```text
SELF_TEST_OK faces=0 emb_shape=(512,) elapsed_ms=67.2
```

Короткий RealSense-прогон:
```text
frame=130 за 5 секунд
MAX_TIME reached: 5.0s
Done.
```

**Текущее решение:** V3 face gate по умолчанию использует `--face-backend trt`. CPU-версия оставлена как fallback через `--face-backend cpu`.

### 4. Нагрев CPU

**Проблема:** Робот нагревается до 80°C при постоянной работе скрипта.

**Решение:** После приветствия скрипт сразу завершается. Нагрузка на CPU только во время поиска (~10–30 сек).

## Модели insightface (buffalo_l)

Расположение: `~/.insightface/models/buffalo_l/`

| Модель | Назначение | Размер |
|--------|------------|--------|
| det_10g.onnx | Детекция лиц (RetinaFace) | 16 MB |
| w600k_r50.onnx | Распознавание (ArcFace, 512-d) | 174 MB |
| 1k3d68.onnx | 3D ландмарки (68 точек) | 143 MB |
| 2d106det.onnx | 2D ландмарки (106 точек) | 5 MB |
| genderage.onnx | Пол и возраст | 1 MB |

Модели скачиваются автоматически при первом запуске insightface.

## TensorRT engines (сконвертированы)

Расположение: `~/.insightface/models/buffalo_l/*.engine`

| Engine | Размер |
|--------|--------|
| det_10g.engine | 16.8 MB |
| w600k_r50.engine | 167.1 MB |
| 2d106det.engine | 5.7 MB |
| 1k3d68.engine | 137.9 MB |
| genderage.engine | 1.9 MB |

Конвертация выполнена скриптом `convert_to_trt.py`.

## Зависимости

### butreq uirements-face.txt:
```
opencv-python>=4.8
insightface>=0.7.3
onnxruntime>=1.16
numpy>=1.24
```

### Дополнительно на роботе:
```
scipy>=1.7,<1.12  # совместимость с numpy 1.24
```

## Связка с V3 (итоговая миссия)

Распознавание лиц теперь можно запускать как **предмиссионный gate** из основной миссии V3 (`itog_v3.py`).

Алгоритм:

```text
itog_v3.py
  → опционально запускает face_recognize.py отдельным процессом
  → face_recognize.py открывает RealSense, ищет Олега, говорит приветствие
  → face_recognize.py завершает работу и освобождает RealSense
  → itog_v3.py запускает обычный YOLO + depth поиск кофе
```

Запуск через основной V3:

```bash
cd /home/unitree/yolo_cup_project/V3
python3 itog_v3.py eth0 \
  --real \
  --motion-backend udp \
  --face-recognition \
  --face-backend trt \
  --face-threshold 0.35 \
  --face-timeout 30 \
  --model /home/unitree/yolo_cup_project/V3/yolo11n_960.engine \
  --img-size 960
```

Если нужно остановить миссию при ошибке/таймауте распознавания:

```bash
--face-required
```

Если нужно проверить распознавание без озвучки:

```bash
--face-recognition --no-face-tts --face-timeout 15
```

Добавленные V3-файлы:

| Файл | Назначение |
|------|------------|
| `itog_v3_core/v3_face.py` | subprocess-wrapper для запуска `face_recognize.py` перед миссией |
| `itog_v3_core/v3_cli.py` | CLI-флаги `--face-*` и вызов face gate |
| `itog_v3.py` | экспорт `FaceRecognitionConfig`, `run_face_recognition_gate` |

Новые CLI-флаги:

| Флаг | Описание |
|------|----------|
| `--face-recognition` | Включить распознавание перед поиском кофе |
| `--face-required` | Падать, если распознавание не отработало |
| `--face-backend` | `trt` для GPU TensorRT, `cpu` для старого insightface/ONNXRuntime |
| `--face-python` | Явный Python для `.venv_face`; по умолчанию берется `V3/.venv_face/bin/python` |
| `--face-script` | Путь к `face_recognize.py` |
| `--face-trt-script` | Путь к `face_recognize_trt.py` |
| `--face-embeddings` | Путь к `.npz` embeddings |
| `--face-threshold` | Порог cosine similarity |
| `--face-timeout` | Максимальное время работы face gate |
| `--face-det-size` | Размер детекции insightface: `320` или `640` |
| `--face-debug-dir` | Папка для `latest.jpg` в headless режиме |
| `--no-face-tts` | Не озвучивать приветствие |

## Что можно улучшить

1. **GPU acceleration** — доделать TensorRT интеграцию или собрать onnxruntime-gpu из исходников
2. **Интеграция с V3** — вызывать распознавание из `itog_v3.py` перед поиском кофе
3. **Несколько людей** — регистрация нескольких человек с разными приветствиями
4. **Continuous mode** — опционально не завершаться после приветствия
5. **Адаптивный порог** — подбирать порог в зависимости от расстояния/освещения

## Файлы для ручного копирования

Если SCP не работает автоматически:

```bash
# Скопировать на робота:
scp face_recognize.py requirements-face.txt oleg_embeddings.npz \
  unitree@192.168.1.102:/home/unitree/yolo_cup_project/V3/

# Установить на роботе:
ssh unitree@192.168.1.102
cd /home/unitree/yolo_cup_project/V3
python3 -m venv --system-site-packages .venv_face
.venv_face/bin/pip install -r requirements-face.txt
.venv_face/bin/pip install "scipy>=1.7,<1.12"

# Скачать модель insightface (автоматически при первом запуске):
.venv_face/bin/python -c "from insightface.app import FaceAnalysis; FaceAnalysis(name='buffalo_l').prepare(ctx_id=0, det_size=(640,640))"
```
