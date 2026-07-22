"""Единый пульт «Кузьмич» — FastAPI-бэкенд.

Один процесс на борту робота: отдаёт веб-пульт (SPA), принимает команды по
WebSocket, проксирует видео (MJPEG) и вызывает мостики подсистем. Вся робото-часть
спрятана за мостиками, поэтому в dry-run пульт полностью работает без робота.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, Set
import os

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import CONFIG
from .state import STATE, MODE_MANUAL, MODE_TELEOP
from .filemanager import router as file_router
from .bridges import arms, ai, hand, prompt, voice
from .bridges.companion import COMPANION
from .bridges.movement import MovementBridge
from .bridges.head import HeadBridge
from .bridges.teleop import TELEOP
from camera import router as camera_router, init_camera, get_camera_manager
from camera.config import CameraConfig

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("cockpit")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="Kuzmich Cockpit")
app.include_router(camera_router)
app.include_router(file_router)

movement = MovementBridge(on_event=lambda level, msg: STATE.log_event(level, msg))
head = HeadBridge()

_clients: Set[WebSocket] = set()


# --------------------------------------------------------------------------- #
# Жизненный цикл
# --------------------------------------------------------------------------- #
@app.on_event("startup")
async def _startup() -> None:
    movement.start()
    # Initialize camera module
    camera_config = CameraConfig()
    init_camera(camera_config)
    # Auto-start camera if not dry_run
    if not CONFIG.dry_run:
        cam = get_camera_manager()
        if cam:
            try:
                await cam.start()
            except Exception as e:
                log.warning("Camera auto-start failed: %s", e)
    asyncio.get_event_loop().create_task(_telemetry_loop())
    log.info("Cockpit up. dry_run=%s  http://%s:%s", CONFIG.dry_run, CONFIG.host, CONFIG.port)


@app.on_event("shutdown")
async def _shutdown() -> None:
    movement.shutdown()
    head.shutdown()
    cam = get_camera_manager()
    if cam:
        await cam.shutdown()


# --------------------------------------------------------------------------- #
# Рассылка телеметрии
# --------------------------------------------------------------------------- #
async def _broadcast(msg: Dict[str, Any]) -> None:
    dead = []
    for ws in list(_clients):
        try:
            await ws.send_json(msg)
        except Exception:  # noqa: BLE001
            dead.append(ws)
    for ws in dead:
        _clients.discard(ws)


async def _telemetry_loop() -> None:
    while True:
        vx, vy, wz = movement.current
        snap = STATE.snapshot()
        cam = get_camera_manager()
        msg = {
            "t": "telemetry",
            "mode": snap["mode"],
            "estop": snap["estop"],
            "dry_run": CONFIG.dry_run,
            "companion": COMPANION.status(),
            "move": {"vx": round(vx, 3), "vy": round(vy, 3), "wz": round(wz, 3)},
            "camera": cam.status() if cam else {"state": "stopped"},
        }
        await _broadcast(msg)
        await asyncio.sleep(0.2)


# --------------------------------------------------------------------------- #
# REST
# --------------------------------------------------------------------------- #
@app.get("/api/config")
async def api_config() -> JSONResponse:
    return JSONResponse({
        "dry_run": CONFIG.dry_run,
        "arm_actions": CONFIG.arm_actions,
        "hand_presets": CONFIG.hand_presets,
        "led_animations": CONFIG.led_animations,
        "eye_macros": CONFIG.eye_macros,
        "ai_models": {"local": CONFIG.ai_model_local, "cloud": CONFIG.ai_model_cloud},
        "limits": {"vx": CONFIG.max_vx, "vy": CONFIG.max_vy, "vyaw": CONFIG.max_vyaw},
    })

# --- State transitions (PUT — идемпотентно, замена состояния) ---

@app.put("/api/state/mode")
async def api_set_mode(data: dict):
    mode = data.get("mode", MODE_MANUAL)
    try:
        STATE.set_mode(mode)
    except ValueError as exc:
        await _emit("err", str(exc))
        return {"error": str(exc), **STATE.snapshot()}
    if mode == MODE_TELEOP:
        movement.stop_move()
        await TELEOP.start()
    else:
        await TELEOP.stop()
    await _emit("info", f"Режим: {'VR-телеоперация' if mode == MODE_TELEOP else 'ручной пульт'}")
    return {"mode": mode, **STATE.snapshot()}

@app.put("/api/state/estop")
async def api_set_estop(data: dict):
    engaged = data.get("engaged", True)
    STATE.set_estop(engaged)
    if engaged:
        movement.stop_move()
        await _emit("err", "АВАРИЙНЫЙ СТОП")
    else:
        await _emit("info", "Аварийный стоп сброшен")
    return {"estop": engaged}

@app.post("/api/movement/stop")
async def api_stop_movement():
    movement.stop_move()
    await _emit("info", "Стоп движения")
    return {"status": "ok"}

# --- Commands: PUT для идемпотентных (задают состояние), POST для неидемпотентных ---

@app.put("/api/command/arm")
async def api_arm_command(data: dict):
    allowed, why = STATE.motion_allowed()
    if not allowed:
        await _emit("warn", f"Рука заблокирована: {why}")
        return {"ok": False, "detail": why}
    action = str(data.get("action", ""))
    loop = asyncio.get_event_loop()
    ok, detail = await loop.run_in_executor(None, arms.send_arm_action, action)
    await _emit("info" if ok else "err", f"Рука '{action}': {detail}")
    return {"ok": ok, "detail": detail}

@app.put("/api/command/hand")
async def api_hand_command(data: dict):
    allowed, why = STATE.motion_allowed()
    if not allowed:
        await _emit("warn", f"Кисть заблокирована: {why}")
        return {"ok": False, "detail": why}
    angles = data.get("angles")
    if not angles:
        preset = str(data.get("preset", ""))
        angles = CONFIG.hand_presets.get(preset)
    if not angles:
        await _emit("err", "Кисть: не заданы углы/пресет")
        return {"ok": False, "detail": "не заданы углы/пресет"}
    loop = asyncio.get_event_loop()
    ok, detail = await loop.run_in_executor(None, hand.write_left_angles, angles)
    await _emit("info" if ok else "err", f"Кисть {list(angles)}: {'ok' if ok else detail}")
    return {"ok": ok, "detail": detail}

@app.put("/api/command/head")
async def api_head_command(data: dict):
    payload = data.get("payload") or {}
    if not isinstance(payload, dict) or not payload.get("cmd"):
        await _emit("err", "Голова: пустая команда")
        return {"ok": False, "detail": "пустая команда"}
    ok, detail = await head.send(payload)
    label = payload.get("name", payload.get("value", payload.get("color", "")))
    await _emit("info" if ok else "err", f"Голова {payload['cmd']} {label}: {detail}")
    return {"ok": ok, "detail": detail}

@app.post("/api/command/tts")
async def api_tts_command(data: dict):
    ok, detail = await voice.speak(str(data.get("text", "")))
    await _emit("info" if ok else "err", f"TTS: {detail}")
    return {"ok": ok, "detail": detail}

@app.post("/api/command/ai")
async def api_ai_command(data: dict):
    source = str(data.get("source", "local"))
    ok, reply = await ai.chat(str(data.get("text", "")), source)
    if ok:
        return {"ok": True, "source": source, "text": reply}
    else:
        await _emit("err", f"ИИ: {reply}")
        return {"ok": False, "detail": reply}

# --------------------------------------------------------------------------- #
# WebSocket управления (только move: {vx, vy, wz})
# --------------------------------------------------------------------------- #
@app.websocket("/ws")
async def ws_control(ws: WebSocket) -> None:
    await ws.accept()
    _clients.add(ws)
    await ws.send_json({"t": "hello", "dry_run": CONFIG.dry_run, "companion": COMPANION.status(), **STATE.snapshot()})
    for ev in STATE.recent_events(20):
        await ws.send_json(ev)
    try:
        while True:
            data = await ws.receive_json()
            vx = data.get("vx", 0.0)
            vy = data.get("vy", 0.0)
            wz = data.get("wz", 0.0)
            if STATE.motion_allowed()[0]:
                movement.set(vx, vy, wz)
            else:
                movement.stop_move()
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("ws error: %s", exc)
    finally:
        _clients.discard(ws)


async def _emit(level: str, msg: str) -> None:
    await _broadcast(STATE.log_event(level, msg))


# -------- Управление системными промтами ИИ --------
@app.get("/api/prompts/list")
async def api_list_prompts():
    return {"prompts": prompt.list_prompts()}

@app.get("/api/prompts/active")
async def api_get_active_prompt():
    import os
    link = CONFIG.active_prompt_link
    if not os.path.lexists(link):
        return {"active": None}
    try:
        target = os.readlink(link)
        filename = os.path.basename(target)
        if os.path.exists(os.path.join(CONFIG.prompts_dir, filename)):
            return {"active": filename}
        else:
            return {"active": None}
    except OSError:
        return {"active": None}

@app.get("/api/prompts/get/{name}")
async def api_get_prompt(name: str):
    try:
        content = prompt.read_prompt(name)
        return {"name": name, "content": content}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Промт не найден")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/prompts/{name}")
async def api_save_prompt(name: str, data: dict):
    content = data.get("content")
    if content is None:
        raise HTTPException(status_code=400, detail="Отсутствует поле 'content'")
    try:
        prompt.write_prompt(name, content)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/prompts/delete/{name}")
async def api_delete_prompt(name: str):
    if prompt.delete_prompt(name):
        return {"status": "ok"}
    else:
        raise HTTPException(status_code=404, detail="Промт не найден")

@app.put("/api/prompts/{name}/select")
async def api_select_prompt(name: str):
    try:
        if prompt.select_prompt(name):
            return {"status": "ok"}
        else:
            raise HTTPException(status_code=500, detail="Не удалось создать ссылку")
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/prompts/{old_name}/rename")
async def api_rename_prompt(old_name: str, data: dict):
    new_name = data.get("new_name")
    if not new_name:
        raise HTTPException(status_code=400, detail="Отсутствует 'new_name'")
    try:
        prompt.rename_prompt(old_name, new_name)
        return {"status": "ok"}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Исходный промт не найден")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --------------------------------------------------------------------------- #
# Управление компаньоном ИИ
# --------------------------------------------------------------------------- #
@app.put("/api/companion/select")
async def api_companion_select(data: dict):
    mode = data.get("mode", "")
    config_name = data.get("config", "")
    if not mode or not config_name:
        raise HTTPException(status_code=400, detail="Отсутствуют 'mode' и 'config'")
    ok, msg = COMPANION.select_config(mode, config_name)
    if ok:
        await _emit("info", f"ИИ: {msg}")
    else:
        await _emit("err", f"ИИ: {msg}")
    return {"ok": ok, "message": msg, **COMPANION.status()}


@app.get("/api/companion/base_config")
async def api_companion_base_config():
    """Читает базовый конфиг (voice_robot/base.ini) — источник дефолтов."""
    try:
        data = COMPANION.read_config_structured_base()
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/companion/status")
async def api_companion_status():
    return COMPANION.status()


@app.put("/api/companion/mode")
async def api_companion_set_mode(data: dict):
    mode = data.get("mode", "off")
    config_name = data.get("config")
    ok, msg = COMPANION.set_mode(mode, config_name)
    if ok:
        await _emit("info", f"ИИ: {msg}")
    else:
        await _emit("err", f"ИИ: {msg}")
    return {"ok": ok, "message": msg, **COMPANION.status()}


@app.get("/api/companion/configs/{mode}")
async def api_companion_list_configs(mode: str):
    if mode not in ("internal", "external"):
        raise HTTPException(status_code=400, detail="mode должен быть 'internal' или 'external'")
    return {"configs": COMPANION.list_configs(mode)}


@app.put("/api/companion/config/{mode}/{name}")
async def api_companion_create_config(mode: str, name: str, data: dict):
    name = data.get("name", name)
    if not name:
        raise HTTPException(status_code=400, detail="Отсутствует 'name'")
    try:
        COMPANION.create_config(mode, name)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/companion/config/{mode}/{old_name}/rename")
async def api_companion_rename_config(mode: str, old_name: str, data: dict):
    new_name = data.get("new_name", "")
    if not new_name:
        raise HTTPException(status_code=400, detail="Отсутствует 'new_name'")
    try:
        COMPANION.rename_config(mode, old_name, new_name)
        return {"status": "ok"}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Исходный конфиг не найден")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/companion/models/{backend}")
async def api_companion_list_models(backend: str):
    """Список моделей из models_dir для заданного бэкенда (llama/vllm)."""
    import configparser
    voice_robot = COMPANION.OVERRIDE_SYMLINK.parent
    base_ini = voice_robot / "base.ini"
    if not base_ini.is_file():
        return {"models": []}
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read(base_ini, encoding="utf-8")
    section = backend if backend in cfg else None
    if not section:
        return {"models": []}
    models_dir_raw = cfg.get(section, "models_dir", fallback="")
    if not models_dir_raw:
        return {"models": []}
    # Resolve relative to voice_robot, absolute as-is
    p = Path(models_dir_raw)
    if p.is_absolute():
        models_dir = p
    else:
        models_dir = (voice_robot / p).resolve()
    if not models_dir.is_dir():
        return {"models": []}
    try:
        models = sorted(
            f for f in os.listdir(models_dir)
            if os.path.isfile(models_dir / f) and not f.startswith(".")
        )
    except OSError:
        models = []
    return {"models": models}


@app.get("/api/companion/config/{mode}/get/{name}")
async def api_companion_get_config(mode: str, name: str):
    try:
        text = COMPANION.read_config(mode, name)
        return {"name": name, "content": text}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Конфиг не найден")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/companion/config/{mode}/{name}")
async def api_companion_save_config(mode: str, name: str, data: dict):
    content_text = data.get("content", "")
    try:
        COMPANION.save_config_raw(mode, name, content_text)
        if COMPANION.active_mode == mode and COMPANION.active_config == name:
            if COMPANION.is_running:
                COMPANION._stop_process()
                COMPANION._start_process()
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/api/companion/config/{mode}/delete/{name}")
async def api_companion_delete_config(mode: str, name: str):
    try:
        if COMPANION.delete_config(mode, name):
            return {"status": "ok"}
        else:
            raise HTTPException(status_code=404, detail="Конфиг не найден")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# --------------------------------------------------------------------------- #
# Teleop (Treelogic) — управление teleop_bridge
# --------------------------------------------------------------------------- #
@app.get("/api/teleop/status")
async def api_teleop_status():
    return await TELEOP.async_status()


@app.put("/api/teleop/start")
async def api_teleop_start():
    ok = await TELEOP.start()
    if ok:
        await _emit("info", "Teleop запущен")
    else:
        await _emit("err", "Не удалось запустить Teleop")
    return {"ok": ok}


@app.put("/api/teleop/stop")
async def api_teleop_stop():
    ok = await TELEOP.stop()
    if ok:
        await _emit("info", "Teleop остановлен")
    else:
        await _emit("err", "Не удалось остановить Teleop")
    return {"ok": ok}


# --------------------------------------------------------------------------- #
# Статика: весь frontend (HTML, CSS, JS) отдаётся из одной папки
# --------------------------------------------------------------------------- #
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
