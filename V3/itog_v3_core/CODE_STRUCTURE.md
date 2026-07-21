# V3 code structure

Корневой `../itog_v3.py` остался совместимым входным файлом. Его можно запускать как раньше:

```bash
python3 itog_v3.py eth0 --real --motion-backend udp --img-size 960
```

Основная логика лежит в этой папке `itog_v3_core`:

- `v3_common.py` - общие константы, `clamp`, rate-limit, логирование событий, TTS-запросы.
- `v3_vision.py` - RealSense color+depth, TensorRT YOLO, расчет `X/Y/Z` стакана.
- `v3_motion.py` - ПИД-подобный расчет скорости, TTL UDP backend, Unitree SDK backend.
- `v3_approach.py` - верхний цикл подхода: поиск цели, потеря цели, досмотр, остановка у стакана.
- `v3_hands.py` - UDP arm-player, Inspire E2 MODBUS, диагностика кистей и внешних процессов.
- `v3_conversation.py` - wake word, STT, короткий диалог через Ollama, TTS.
- `v3_cli.py` - аргументы командной строки и сборка всех узлов в один сценарий.
- `../see_for_robot_v3.py` - отдельный web-stream с YOLO-интерфейсом, использует публичные классы из `../itog_v3.py`.

Публичные классы и функции реэкспортируются из `itog_v3.py`, поэтому старые импорты вида:

```python
from itog_v3 import VisionConfig, YoloDepthCupDetector
```

остаются рабочими.

## Smoke-test without motion

```bash
cd /home/unitree/yolo_cup_project/V3
python3 itog_v3.py eth0 \
  --model /home/unitree/yolo_cup_project/V3/yolo11n_960.engine \
  --img-size 960 --conf 0.05 --target-class cup \
  --max-time 5 --dt 0.02 \
  --no-conversation --no-coffee-request --no-arm-reach \
  --quiet --keep-external-processes --profile-every 30
```

## Real run

```bash
cd /home/unitree/yolo_cup_project/V3
python3 itog_v3.py eth0 \
  --real --motion-backend udp \
  --model /home/unitree/yolo_cup_project/V3/yolo11n_960.engine \
  --img-size 960 --conf 0.05 --target-class cup \
  --dt 0.02 --quiet --profile-every 30
```

## Current default model

V3 default model is:

```text
/home/unitree/yolo_cup_project/V3/yolo11n_960.engine
```

## Voice/Ollama run

Voice dependencies are listed in `../requirements-voice.txt`.

The V3 voice launcher is:

```bash
cd /home/unitree/yolo_cup_project/V3
./run_voice_robot.sh
```

It uses USB microphone device `0`, starts `ollama serve` if needed, and sets the default conversation model to local `gemma4:e2b`.
