# STT/TTS/Microphone report for Unitree G1 V3

Дата: 2026-07-09.

## Контекст

Робот: Unitree G1.

Проект на роботе:

```text
/home/unitree/yolo_cup_project/V3
```

Главный файл:

```text
/home/unitree/yolo_cup_project/V3/itog_v3.py
```

Голосовой модуль:

```text
/home/unitree/yolo_cup_project/V3/itog_v3_core/v3_conversation.py
```

Запускной файл:

```text
/home/unitree/yolo_cup_project/V3/run_voice_robot.sh
```

В V3 восстановлено отдельное окружение:

```text
/home/unitree/yolo_cup_project/V3/.venv_voice
```

Установлены зависимости:

```text
ollama
SpeechRecognition
sounddevice
```

Системно установлен `flac`, он нужен для `SpeechRecognition.recognize_google`.

## Как должен работать голосовой сценарий

1. Робот ждет wake-word:

```text
Кузьмич
```

2. После wake-word робот говорит:

```text
слушаю тебя
```

3. Робот слушает вопрос пользователя.

4. STT переводит голос в текст через Google STT.

5. Текст отправляется в Ollama local model:

```text
gemma4:e2b
```

6. Ответ модели озвучивается через TTS endpoint робота.

## TTS

TTS endpoint:

```text
http://192.168.1.102/api/audio/tts
```

Рабочие настройки:

```text
hardware_volume=85
amplification_db=13.0
```

Проверка TTS:

```bash
curl -sS -X POST 'http://192.168.1.102/api/audio/tts' \
  -H 'Content-Type: application/json' \
  -d '{"text":"проверка голоса","play":true,"hardware_volume":85,"amplification_db":13}'
```

Ожидаемый результат:

```json
{"ok": true, ...}
```

Фактически TTS работал: робот произносил тестовые фразы.

## Ollama

На роботе есть модели:

```text
gemma4:e2b
gemma4:31b-cloud
qwen2.5:3b
```

Проверка Ollama:

```bash
ollama list
```

Проверка генерации через API:

```bash
curl -sS http://127.0.0.1:11434/api/generate \
  -d '{"model":"gemma4:e2b","prompt":"Ответь коротко по-русски: как дела?","stream":false}'
```

Рабочий пример ответа:

```text
Хорошо, а у тебя?
```

Важно: Python-клиент `ollama.chat()` и `ollama.generate()` в текущем окружении давали пустой ответ для `gemma4:e2b`. Поэтому в `v3_conversation.py` функция `_ask_ollama()` переписана на прямой HTTP-запрос:

```text
http://127.0.0.1:11434/api/generate
```

Еще важно: `gemma4:e2b` возвращала пустой ответ на некоторые сложные prompt-формулировки. Рабочая простая формулировка:

```text
Ответь коротко по-русски: <текст пользователя>
```

## Микрофон / STT

Подключался внешний USB audio device:

```text
AB13X USB Audio
```

Команда просмотра устройств:

```bash
/home/unitree/yolo_cup_project/V3/.venv_voice/bin/python - <<'PY'
import sounddevice as sd
print(sd.query_devices())
PY
```

На момент проверки `sounddevice` показывал:

```text
0 AB13X USB Audio ... 0 in, 2 out
37 default ... 32 in, 32 out
```

Но `arecord -l` видел USB capture:

```bash
arecord -l
```

Пример:

```text
card 0: Audio [AB13X USB Audio], device 0: USB Audio [USB Audio]
```

PulseAudio видел USB source:

```bash
pactl list short sources
```

Пример:

```text
alsa_input.usb-Generic_AB13X_USB_Audio_20210926172016-00.mono-fallback
```

Для V3 `run_voice_robot.sh` сейчас использует:

```text
--conversation-input-device pulse
```

А не `0`, потому что `device 0` через `sounddevice` оказался output-only.

## Что было не так

TTS работал.

Ollama API работал.

Но STT не распознавал голос.

Прямой тест записи через `arecord`:

```bash
arecord -D hw:0,0 -f S16_LE -r 48000 -c 1 -d 6 /tmp/voice_arecord.wav
```

Дал почти тишину:

```text
WAV rate=48000 width=2 channels=1 bytes=576000 rms=9 peak=254
RECOGNIZED=None
```

Для 16-bit audio `rms=9` и `peak=254` очень мало. Это похоже не на проблему STT, а на проблему входного аудио:

- микрофон был плохо подключен;
- выбран не тот input source;
- USB audio device был output-only в `sounddevice`;
- capture gain/source в Pulse/ALSA не давал нормальный голос.

Mixer USB-карты был проверен:

```bash
amixer -c 0
```

На момент проверки:

```text
Mic Capture Switch: on
Mic Capture Volume: 100% / 31.99 dB
```

То есть mute/gain были нормальные, но реальный звук почти не попадал в capture.

## Быстрая диагностика для друга

Подключиться к роботу:

```bash
ssh unitree@192.168.1.102
```

Пароль:

```text
123
```

Если спросит ROS:

```text
ros:foxy(1) noetic(2) ?
```

Выбрать:

```text
1
```

Перейти в V3:

```bash
cd /home/unitree/yolo_cup_project/V3
```

Проверить TTS:

```bash
curl -sS -X POST 'http://192.168.1.102/api/audio/tts' \
  -H 'Content-Type: application/json' \
  -d '{"text":"проверка голоса","play":true,"hardware_volume":85,"amplification_db":13}'
```

Проверить Ollama:

```bash
curl -sS http://127.0.0.1:11434/api/generate \
  -d '{"model":"gemma4:e2b","prompt":"Ответь коротко по-русски: как дела?","stream":false}'
```

Проверить источники Pulse:

```bash
pactl list short sources
```

Поставить USB source как default:

```bash
pactl set-default-source alsa_input.usb-Generic_AB13X_USB_Audio_20210926172016-00.mono-fallback
pactl set-source-mute alsa_input.usb-Generic_AB13X_USB_Audio_20210926172016-00.mono-fallback 0
pactl set-source-volume alsa_input.usb-Generic_AB13X_USB_Audio_20210926172016-00.mono-fallback 150%
```

Проверить ALSA capture:

```bash
arecord -l
arecord -D hw:0,0 -f S16_LE -r 48000 -c 1 -d 6 /tmp/voice_arecord.wav
ls -lh /tmp/voice_arecord.wav
```

Проверить уровень WAV:

```bash
/home/unitree/yolo_cup_project/V3/.venv_voice/bin/python - <<'PY'
import wave, audioop
with wave.open('/tmp/voice_arecord.wav', 'rb') as w:
    raw = w.readframes(w.getnframes())
    print('rate', w.getframerate(), 'channels', w.getnchannels(), 'width', w.getsampwidth())
    print('rms', audioop.rms(raw, w.getsampwidth()), 'peak', audioop.max(raw, w.getsampwidth()))
PY
```

Нормальный голос должен давать сильно больше, чем:

```text
rms=9 peak=254
```

Если RMS остается около нуля, надо физически переподключить микрофон или выбрать другой input source.

## Тест STT на записанном WAV

После записи `/tmp/voice_arecord.wav`:

```bash
/home/unitree/yolo_cup_project/V3/.venv_voice/bin/python - <<'PY'
import sys, wave
sys.path.insert(0, '/home/unitree/yolo_cup_project/V3')
sys.path.insert(0, '/home/unitree/yolo_cup_project/V3/itog_v3_core')
from v3_conversation import _check_conversation_imports, _recognize_speech

_, _, sr = _check_conversation_imports()
with wave.open('/tmp/voice_arecord.wav', 'rb') as w:
    raw = w.readframes(w.getnframes())
    rate = w.getframerate()
text = _recognize_speech(sr, raw, sample_rate=rate, language='ru-RU')
print('RECOGNIZED=', repr(text))
PY
```

Если `RECOGNIZED=None`, проблема еще на уровне аудио/STT.

## Запуск голосового режима V3

После того как микрофон реально пишет речь:

```bash
cd /home/unitree/yolo_cup_project/V3
./run_voice_robot.sh
```

Важно: этот скрипт запускает основной сценарий робота в `--real`, то есть после голосового gate робот может перейти к миссии. Для чистого теста голоса лучше запускать отдельный voice-only тест, без движения.

## Что править в коде

Файл:

```text
/home/unitree/yolo_cup_project/V3/itog_v3_core/v3_conversation.py
```

Важные параметры:

```python
ConversationConfig(
    model="gemma4:e2b",
    sample_rate=48000,
    sound_threshold=0.015,
    input_device="0",
)
```

Если микрофон после переподключения дает нормальный RMS, можно подобрать `sound_threshold`.

Если wake-word плохо ловится, временно лучше тестировать без wake-word: сначала фиксированная запись 5-6 секунд, потом STT.

## Текущий вывод

На момент последней проверки:

- TTS работает.
- Ollama работает через HTTP API.
- `gemma4:e2b` отвечает, если prompt простой.
- `flac` установлен.
- Новый USB-микрофон работает: `Jieli Technology USB Composite Device`, ALSA/sounddevice device `0`.
- `run_voice_robot.sh` теперь запускает V3 с `--conversation-input-device 0`.
- `.venv_voice` дополнен пакетом `numpy`, он нужен для записи через `sounddevice`.

## Обновление 2026-07-10: новый микрофон

Устройство:

```text
sounddevice index: 0
name: USB Composite Device: Audio (hw:0,0)
ALSA: hw:0,0
Pulse source: alsa_input.usb-Jieli_Technology_USB_Composite_Device_...mono-fallback
```

Проверка через `arecord`:

```text
rate: 48000 Hz
channels: 1
rms: 5190
peak: 30462
peak_dbfs: -0.6
rms_dbfs: -16.0
```

Проверка через `sounddevice` из `.venv_voice`:

```text
device 0: USB Composite Device: Audio (hw:0,0)
sounddevice_rms 0.01006
peak 0.39838
threshold_default 0.015
```

Проверка Google STT через тот же код, который использует V3:

```text
audio_bytes 403200
RECOGNIZED= 'Кузьмич Привет'
```

## Обновление 2026-07-10: системный промт Кузьмича

Файл системного промта:

```text
/home/unitree/yolo_cup_project/V3/kuzmich_system_prompt.txt
```

V3 передает его в Ollama через поле `system` в `/api/generate`.

Важный нюанс: полный файл около `10k` символов на `gemma4:e2b` возвращал пустой ответ. Рабочий режим — передавать первые `3000` символов:

```bash
--conversation-system-prompt /home/unitree/yolo_cup_project/V3/kuzmich_system_prompt.txt
--conversation-system-prompt-max-chars 3000
```

Проверка:

```text
prompt_len 3000
ANSWER= Ах, милок... Кто я? Я же Кузьмич...
```

Файл тестовой записи на ноуте:

```text
/home/darknight/Документы/robot/V3/mic_tests/new_mic_test.wav
```
