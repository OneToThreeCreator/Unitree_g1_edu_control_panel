# Управление головой робота на Arduino Nano

## Что это

Голова управляется через Arduino Nano. Робот или компьютер отправляет текстовые команды по USB/Serial, а Nano управляет:

- двумя LED-кольцами WS2812B, всего 16 светодиодов;
- двумя сервоприводами лючков глаз.

Файлы:

- прошивка Nano: `nano_led_blink/nano_led_blink.ino`
- Python-оболочка: `nano_controller.py`

## Подключение

### LED-кольца

```text
Nano D6 -> резистор 330-470 Ом -> DI первого кольца
5V      -> 5V первого кольца
GND     -> GND первого кольца

DO первого кольца -> DI второго кольца
5V первого кольца -> 5V второго кольца
GND первого кольца -> GND второго кольца
```

### Сервы

```text
Nano D9  -> сигнал сервы 1
Nano D10 -> сигнал сервы 2

Доп 5V  -> красные провода серв
Доп GND -> черные/коричневые провода серв
Nano GND -> общий GND доп питания
```

Важно: сервы нельзя нормально питать от `5V` Nano/USB. Нужно отдельное 5V питание и общая земля с Nano.

## Заливка прошивки

Плата у нас Nano на старом загрузчике:

```bash
arduino-cli compile --fqbn arduino:avr:nano:cpu=atmega328old /home/darknight/Документы/robot/nano_led_blink
arduino-cli upload -p /dev/ttyUSB0 --fqbn arduino:avr:nano:cpu=atmega328old /home/darknight/Документы/robot/nano_led_blink
```

Если нет доступа к порту:

```bash
sudo chmod a+rw /dev/ttyUSB0
```

Порт может быть не `/dev/ttyUSB0`. Проверить:

```bash
arduino-cli board list
ls -l /dev/ttyUSB* /dev/ttyACM*
```

## Python-оболочка

Запуск интерактивного режима:

```bash
python3 /home/darknight/Документы/robot/nano_controller.py shell --port /dev/ttyUSB0
```

Разовая команда:

```bash
python3 /home/darknight/Документы/robot/nano_controller.py loading_rainbow --port /dev/ttyUSB0
```

Лучше использовать `shell`, если надо отправить несколько команд подряд: открытие serial часто перезагружает Arduino Nano.

## Команды подсветки

```text
red                     - все светодиоды красные
primary                 - переключение красный/зеленый/синий
rainbow                 - переключение цветов радуги
loading                 - обычная синяя загрузка
loading_rainbow         - загрузка со сменой цветов радуги
half                    - включить половину каждого кольца
off                     - выключить подсветку
clear                   - очистить все светодиоды
brightness 0..255       - яркость
rgb R G B               - залить все светодиоды цветом
pixel INDEX R G B       - задать один светодиод
fill START COUNT R G B  - залить диапазон светодиодов
loading_color R G B     - цвет обычной загрузки
loading_speed MS        - скорость загрузки, 30..2000 мс
half_color R G B        - цвет режима half
```

Индексы светодиодов:

```text
0..7   - первое кольцо
8..15  - второе кольцо
```

Примеры:

```text
brightness 60
rgb 255 0 0
pixel 0 0 255 0
fill 0 8 0 0 255
loading_speed 120
loading_rainbow
```

## Команды серв

```text
servos      - покрутить обе сервы
servos30    - небольшой ход 75-105 градусов
left        - покрутить серву на D9
right       - покрутить серву на D10
center      - поставить обе сервы на 90 градусов
open        - поставить обе сервы на 135 градусов
close       - поставить обе сервы на 45 градусов
servo_off   - отключить управляющие импульсы серв
```

## Пример для программы робота

Минимальный пример Python-кода:

```python
import time
import serial

PORT = "/dev/ttyUSB0"

with serial.Serial(PORT, 115200, timeout=1, write_timeout=1) as head:
    time.sleep(2.2)  # Nano перезагружается при открытии serial
    head.read(1024)

    head.write(b"LOADING_RAINBOW\n")
    head.flush()
```

Несколько команд подряд:

```python
import time
import serial

PORT = "/dev/ttyUSB0"

def send(head, command):
    head.write((command + "\n").encode())
    head.flush()
    time.sleep(0.25)
    return head.read(1024).decode(errors="replace").strip()

with serial.Serial(PORT, 115200, timeout=1, write_timeout=1) as head:
    time.sleep(2.2)
    head.read(1024)

    print(send(head, "BRIGHTNESS 60"))
    print(send(head, "LOADING_SPEED 120"))
    print(send(head, "LOADING_RAINBOW"))
    print(send(head, "SERVOS_30"))
```

## Что включать при старте робота

Рекомендуемый стартовый набор:

```text
BRIGHTNESS 60
LOADING_SPEED 120
LOADING_RAINBOW
```

Для спокойного синего режима:

```text
BRIGHTNESS 60
LOADING_COLOR 0 120 255
LOADING_SPEED 120
LOADING
```

## Новая схема 18 LED

Адресация:

```text
0..7    - первое LED-кольцо
8..15   - второе LED-кольцо
16      - центральный LED 1
17      - центральный LED 2
```

Центральные LED стоят в конце DATA-цепочки:

```text
Nano D6 -> кольцо 1 -> кольцо 2 -> центр 1 -> центр 2
```

Быстрые команды для центральной линии:

```text
CENTER_GREEN
CENTER_RED
CENTER_WHITE
CENTER_OFF
```

Новые анимации:

```text
ANIME_FRIENDLY   - зеленая дружелюбная линия
ANIME_ANGER      - красная линия злости
ANIME_LOADING    - радужная загрузка с центральными LED
ANIME_LINE       - цветная центральная линия
ANIME_LOOP       - крутит эмоции по кругу
```

Через Python:

```bash
python3 /home/unitree/yolo_cup_project/V3/head_emotions.py anime-loop --brightness 120 --speed 120
python3 /home/unitree/yolo_cup_project/V3/head_emotions.py anime-line --brightness 120 --speed 90
python3 /home/unitree/yolo_cup_project/V3/head_emotions.py green-led --led 16
python3 /home/unitree/yolo_cup_project/V3/head_emotions.py green-led --led 17
```

## Если что-то не работает

Если сервы двигаются и Nano отваливается от USB, проблема почти всегда в питании серв. Нужно отдельное 5V питание и общий GND.

Если LED-кольца светятся странными цветами или мерцают, проверь:

- `DI` первого кольца подключен к `D6`;
- стоит резистор 330-470 Ом в линии DATA;
- общий GND есть у Nano и питания светодиодов;
- питание светодиодов 5V.
