# Unitree G1 Edu — Голосовой робот-компаньон

Монорепозиторий проекта:.voice-driven AI companion for Unitree G1 robot.

## Структура

```
├── voice_robot/          # Ядро: голосовой интерфейс и AI-компаньон
├── kuzmich-cockpit/      # Веб-пульт управления (FastAPI + SPA)
├── V3/                   # Модули управления роботом (движение, руки, голова, зрение)
└── head_unitree_custom/  # Прошивка ESP32 умной головы (LED + сервы)
```

### voice_robot

Голосовой робот-компаньон «Кузьмич» — основной скрипт проекта.

| Файл | Описание |
|------|----------|
| `kuzmich_companion.py` | Точка входа: STT → LLM → TTS |
| `conversation.py` | Базовый класс диалога |
| `conversation_llama.py` | Бэкенд: llama-server (локальная модель) |
| `conversation_vllm.py` | Бэкенд: vLLM (Realtime API / HTTP) |
| `base.ini` | Базовая конфигурация |
| `configs/` | Override-конфиги (internal / external) |
| `prompts/` | Системные промты |
| `llama_models/` | GGUF-модели (не в git) |
| `vllm_models/` | Модели для vLLM (не в git) |

### kuzmich-cockpit

Веб-интерфейс для управления роботом и конфигурацией AI.

```bash
cd kuzmich-cockpit && python run.py
# http://0.0.0.0:8080
```

Экраны:
- **Главная** — пульт: движение, руки, голова, TTS, AI
- **Конфигурация ИИ** — переключение моделей и параметров
- **Редактор промтов** — системные промты для AI
- **Файловый менеджер** — просмотр и редактирование файлов

### V3

Модули робота (jetson/robot):

| Модуль | Протокол |
|--------|----------|
| `v3_motion.py` | UDP 127.0.0.1:15100 |
| `v3_hands.py` | UDP 127.0.0.1:15001 + MODBUS TCP |
| `v3_head.py` | Serial /dev/ttyUSB0 (Arduino) |
| `see_for_robot_v3.py` | MJPEG 127.0.0.1:8091 |
| `itog_v3_core/` | Ядро: CLI, vision, approach |

### head_unitree_custom

Прошивка ESP32-C3 для «умной головы»:
- WS2812B LED-ленты (анимации)
- Сервы для глаз (моргание, подмигивание)
- Web UI для управления (`web/index.html`)

## Запуск

### Голосовой робот

```bash
cd voice_robot
pip install -r requirements-voice.txt
python kuzmich_companion.py
```

### Веб-пульт

```bash
cd kuzmich-cockpit
pip install -r requirements.txt
python run.py
```

## Конфигурация

Конфиги загружаются в порядке приоритета:

1. `voice_robot/base.ini` — дефолты
2. `voice_robot/override.ini` → symlink на один из:
   - `override_internal.ini` — внутренний AI (llama/vllm)
   - `override_external.ini` — внешний AI (OpenAI API)

Переключение через веб-пульт или SIGUSR1.

## Стек

- **Backend**: Python 3.8+, FastAPI, uvicorn
- **Frontend**: Vanilla JS (SPA)
- **AI**: Ollama / llama.cpp / vLLM / OpenAI API
- **STT**: Google Speech Recognition
- **TTS**: Self-hosted (Coqui / XTTS)
- **Robot**: Unitree G1 SDK, UDP, MODBUS
