import asyncio
import json
import sys
import argparse
from websockets.asyncio.client import connect

# Доступные режимы анимации (из прошивки)
AVAILABLE_MODES = [
    "off", "static", "rainbow", "rainbow_cycle", "chase",
    "breathing", "theater", "wipe", "scanner", "dual"
]

async def send_command(websocket, cmd, **kwargs):
    """Отправляет команду в формате JSON и выводит ответ (если есть)."""
    payload = {"cmd": cmd}
    payload.update(kwargs)
    await websocket.send(json.dumps(payload))
    # Ожидаем ответ (сервер может ответить state или heartbeat)
    try:
        response = await asyncio.wait_for(websocket.recv(), timeout=1.0)
        data = json.loads(response)
        if data.get("type") == "state":
            print("\n[Текущее состояние]")
            print(f"  Анимация: {data.get('animation')}")
            print(f"  Цвет:     {data.get('color')}")
            print(f"  Яркость:  {data.get('brightness')}")
            print(f"  Скорость: {data.get('speed')}")
            # Можно вывести и другую информацию
        else:
            print(f"[Ответ] {data}")
    except asyncio.TimeoutError:
        print("[Команда отправлена, ответ не получен]")
    except json.JSONDecodeError:
        print("[Получен не JSON-ответ]")

async def interactive(host):
    """Интерактивный режим с выбором из меню."""
    uri = f"ws://{host}:81"
    print(f"Подключение к {uri} ...")
    try:
        async with connect(uri) as websocket:
            print("Подключено! Для выхода введите 'quit' или 'exit'.")
            # Сначала получаем текущее состояние
            await send_command(websocket, "get_state")

            while True:
                print("\nДоступные команды:")
                print("  1. Выбрать режим анимации")
                print("  2. Установить цвет")
                print("  3. Установить яркость")
                print("  4. Установить скорость")
                print("  5. Запросить состояние")
                print("  6. Выход")
                choice = input("Ваш выбор (1-6): ").strip()

                if choice == "1":
                    print("Доступные режимы:", ", ".join(AVAILABLE_MODES))
                    mode = input("Введите имя режима: ").strip().lower()
                    if mode in AVAILABLE_MODES:
                        await send_command(websocket, "led_animation", name=mode)
                    else:
                        print("Неизвестный режим")
                elif choice == "2":
                    color = input("Введите цвет в формате #RRGGBB (например #ff00ff): ").strip()
                    if color.startswith("#") and len(color) == 7:
                        await send_command(websocket, "led_color", color=color)
                    else:
                        print("Неверный формат цвета")
                elif choice == "3":
                    brightness = input("Введите яркость (0-255): ").strip()
                    if brightness.isdigit():
                        b = int(brightness)
                        if 0 <= b <= 255:
                            await send_command(websocket, "led_brightness", value=b)
                        else:
                            print("Яркость должна быть от 0 до 255")
                    else:
                        print("Введите число")
                elif choice == "4":
                    speed = input("Введите скорость (10-500 мс): ").strip()
                    if speed.isdigit():
                        s = int(speed)
                        if 10 <= s <= 500:
                            await send_command(websocket, "led_speed", value=s)
                        else:
                            print("Скорость должна быть от 10 до 500")
                    else:
                        print("Введите число")
                elif choice == "5":
                    await send_command(websocket, "get_state")
                elif choice in ("6", "quit", "exit"):
                    break
                else:
                    print("Неверный выбор")

    except Exception as e:
        print(f"Ошибка: {e}")
        sys.exit(1)

async def single_command(host, mode=None, color=None, brightness=None, speed=None):
    """Однократная отправка команд (по аргументам командной строки)."""
    uri = f"ws://{host}:81"
    try:
        async with connect(uri) as websocket:
            # Сначала запросим состояние (для наглядности)
            await send_command(websocket, "get_state")
            if mode:
                await send_command(websocket, "led_animation", name=mode)
            if color:
                await send_command(websocket, "led_color", color=color)
            if brightness is not None:
                await send_command(websocket, "led_brightness", value=brightness)
            if speed is not None:
                await send_command(websocket, "led_speed", value=speed)
            # Подождём немного, чтобы получить возможные ответы
            await asyncio.sleep(0.5)
    except Exception as e:
        print(f"Ошибка: {e}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Клиент управления светодиодами ESP32")
    parser.add_argument("--host", required=True, help="IP-адрес ESP32 (например 192.168.4.1)")
    parser.add_argument("--mode", choices=AVAILABLE_MODES, help="Режим анимации")
    parser.add_argument("--color", help="Цвет в формате #RRGGBB")
    parser.add_argument("--brightness", type=int, choices=range(0, 256), help="Яркость 0-255")
    parser.add_argument("--speed", type=int, choices=range(10, 501), help="Скорость 10-500 мс")
    args = parser.parse_args()

    if any([args.mode, args.color, args.brightness is not None, args.speed is not None]):
        # Если хотя бы один параметр задан – выполняем однократную отправку
        asyncio.run(single_command(args.host, args.mode, args.color, args.brightness, args.speed))
    else:
        # Иначе интерактивный режим
        asyncio.run(interactive(args.host))

if __name__ == "__main__":
    main()
