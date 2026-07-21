# Прошивка LED-глаз на Arduino Nano

Эта версия рассчитана на два кольца `WCMCU-2812B-8`: по 8 светодиодов на глаз, всего 16 светодиодов.

## Распиновка

```text
Arduino Nano D6  -> DI первого кольца
Arduino Nano 5V  -> 5V обоих колец
Arduino Nano GND -> GND обоих колец

DO первого кольца -> DI второго кольца
DO второго кольца -> никуда
```

## Настройки Arduino IDE

```text
Board: Arduino Nano
Processor: ATmega328P Old Bootloader
Port: /dev/ttyUSB0
Library: FastLED
```

## Параметры прошивки

```cpp
#define DATA_PIN 6
#define NUM_LEDS 16
#define LED_TYPE WS2812B
#define COLOR_ORDER GRB
```

## Serial-команды

Скорость Serial Monitor:

```text
115200
```

Команды:

```text
OFF
RED
PRIMARY
RAINBOW
LOADING
HALF
```

Красный:

```text
RED
```
