# Отчет: голос Кузьмича / кастомный TTS-голос

Дата: 2026-07-10

## Короткий вывод

Родной голосовой модуль робота использует `robot-admin.service` на порту `80` и TTS endpoint:

```text
http://192.168.1.102/api/audio/tts
```

Внутри это RHVoice через native-библиотеку:

```text
/home/unitree/teleop/app/tts/cpp/build/libg1_native.so
```

Доступные голоса сейчас:

```text
aleksandr-hq
arina
artemiy
elena
irina
pavel
```

Голоса лежат здесь:

```text
/usr/local/share/RHVoice/voices
```

Нового голоса Бурунова на роботе нет.

## Важное ограничение

Я не закладываю в проект клонирование голоса реального человека без прав/согласия. Для легальной реализации нужны права на голос/датасет или нужно делать авторский неидентифицирующий голос “в духе персонажа”, но не имитацию конкретного человека.

## Как сейчас V3 выбирает голос

В V3 уже есть параметр:

```bash
--tts-voice
```

Пример:

```bash
cd /home/unitree/yolo_cup_project/V3
./run_voice_robot.sh --tts-voice pavel
```

Все вызовы V3 идут через общий `speak_text(...)`, который передает поле `voice` в `/api/audio/tts`.

## Проверка родного API

Список голосов:

```bash
curl -sS http://192.168.1.102/api/audio/tts/voices
```

Синтез без проигрывания:

```bash
curl -sS -X POST 'http://192.168.1.102/api/audio/tts' \
  -H 'Content-Type: application/json' \
  -d '{"text":"Проверка голоса Павел","voice":"pavel","play":false}'
```

Синтез с проигрыванием:

```bash
curl -sS -X POST 'http://192.168.1.102/api/audio/tts' \
  -H 'Content-Type: application/json' \
  -d '{"text":"Проверка голоса Павел","voice":"pavel","play":true,"hardware_volume":85,"amplification_db":13}'
```

## Как устроен RHVoice voice package

Пример голоса:

```text
/usr/local/share/RHVoice/voices/artemiy
├── voice.info
├── voice.params
├── 16000/
│   ├── voice.data
│   ├── mgc.pdf
│   ├── lf0.pdf
│   ├── bap.pdf
│   ├── dur.pdf
│   ├── tree-*.inf
│   └── *.win*
└── 24000/
    ├── voice.data
    ├── mgc.pdf
    ├── lf0.pdf
    ├── bap.pdf
    ├── dur.pdf
    ├── tree-*.inf
    └── *.win*
```

`voice.info` содержит имя, язык, пол и формат.

Чтобы “родной модуль” увидел новый голос, нужен полноценный RHVoice voice package в:

```text
/usr/local/share/RHVoice/voices/<voice_id>
```

После установки надо перезапустить:

```bash
sudo systemctl restart robot-admin.service
```

И проверить:

```bash
curl -sS http://127.0.0.1/api/audio/tts/voices
```

## Реальные варианты сделать нужный голос

### Вариант A. Нативный RHVoice-голос

Лучше всего интегрируется с родным модулем.

Что нужно:

- легальный датасет голоса;
- обучение RHVoice voice package;
- установка результата в `/usr/local/share/RHVoice/voices/<voice_id>`;
- проверка через `/api/audio/tts/voices`;
- запуск V3 с `--tts-voice <voice_id>`.

Плюсы:

- полностью родной путь;
- работает через существующий `robot-admin.service`;
- не нужно менять V3;
- можно выбирать голос через `--tts-voice`.

Минусы:

- обучение RHVoice-голоса не делается “из пары фраз”;
- нужен подготовленный корпус и права на использование голоса;
- качество может быть ниже современных neural TTS.

### Вариант B. Отдельный neural TTS/voice-conversion service

Например, отдельный сервис генерирует WAV, а робот проигрывает его через native audio player.

Плюсы:

- больше шансов получить похожий тембр;
- можно делать более современное качество.

Минусы:

- это уже не чисто родной RHVoice;
- нужен отдельный inference pipeline;
- нужна GPU/CPU оценка, задержка и стабильность;
- для голоса конкретного человека все равно нужны права.

### Вариант C. Авторский “Кузьмич”-голос на базе существующего RHVoice

Практичный и безопасный вариант на ближайший тест:

- выбрать ближайший мужской голос: `pavel`, `artemiy` или `aleksandr-hq`;
- подобрать `relative_rate`, громкость и усиление;
- оставить системный промт Кузьмича для манеры речи;
- не имитировать конкретного человека.

Запуск:

```bash
./run_voice_robot.sh --tts-voice pavel
```

## Что уже проверено

Native wrapper:

```text
native_available True
voices:
aleksandr-hq
arina
artemiy
elena
irina
pavel
```

Native synthesis разными голосами действительно дает разные WAV:

```text
aleksandr-hq 211564 bytes
artemiy     133804 bytes
pavel       200364 bytes
irina       220844 bytes
```

HTTP endpoint принимает `voice`:

```text
POST /api/audio/tts {"text":"Тест голоса Павел","voice":"pavel","play":false}
ok=true
```

## Следующий практический шаг

1. Выбрать временный голос для Кузьмича из текущих: `pavel`, `artemiy`, `aleksandr-hq`.
2. Прогнать 3 коротких TTS-теста и выбрать лучший тембр.
3. Если нужен именно отдельный кастомный голос, подготовить легальный датасет/пакет RHVoice.
4. После получения voice package установить его как:

```text
/usr/local/share/RHVoice/voices/kuzmich
```

5. Запускать:

```bash
./run_voice_robot.sh --tts-voice kuzmich
```
