"""Мостик управления kuzmich_companion.py.

Управляет запуском/остановкой процесса и переключением конфигураций
через symlink + SIGUSR1.
"""
from __future__ import annotations

import configparser
import logging
import os
import signal
import sys
import subprocess
import threading
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("cockpit.companion")

# Путь к ai (voice_robot) — kuzmich-cockpit/ai
# __file__ = .../kuzmich-cockpit/backend/bridges/companion.py
# parent x3 = .../kuzmich-cockpit/
VOICEROBOT_DIR = Path(__file__).resolve().parent.parent.parent / "ai"
KUZMICH_INI = VOICEROBOT_DIR / "base.ini"
COMPANION_SCRIPT = VOICEROBOT_DIR / "kuzmich_companion.py"

# Директории конфигов — теперь в voice_robot/configs
CONFIGS_DIR = VOICEROBOT_DIR / "configs"


class CompanionManager:
    """Управление процессом kuzmich_companion.py и переключением конфигов."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None
        self._active_mode: str = "off"  # "off" | "internal" | "external"
        self._active_config: Optional[str] = None  # имя файла конфига
        self._last_config: Dict[str, str] = self._load_last_config()

    def _load_last_config(self) -> Dict[str, str]:
        """Восстанавливает _last_config из override_{mode}.ini symlink-ов."""
        result: Dict[str, str] = {}
        for mode, link in [("internal", self.OVERRIDE_INTERNAL), ("external", self.OVERRIDE_EXTERNAL)]:
            if not link.is_symlink():
                continue
            try:
                target = os.readlink(link)
                target_path = Path(target)
                if not target_path.is_absolute():
                    target_path = (link.parent / target_path).resolve()
                config_name = target_path.name
                if config_name.endswith(".ini"):
                    config_name = config_name[:-4]
                if config_name:
                    result[mode] = config_name
            except OSError:
                pass
        return result

    @property
    def is_running(self) -> bool:
        with self._lock:
            if self._process is None:
                return False
            return self._process.poll() is None

    @property
    def pid(self) -> Optional[int]:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return self._process.pid
            return None

    @property
    def active_mode(self) -> str:
        return self._active_mode

    @property
    def active_config(self) -> Optional[str]:
        return self._active_config

    def status(self) -> Dict:
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            # Если _last_config пуст — подтягиваем из symlink'ов
            if not self._last_config:
                self._last_config = self._load_last_config()
            return {
                "mode": self._active_mode,
                "running": running,
                "pid": self._process.pid if running else None,
                "config": self._active_config,
                "last_config": dict(self._last_config),
            }

    # --- Config management ---

    @staticmethod
    def _ensure_ini(name: str) -> str:
        """Гарантирует расширение .ini."""
        safe = os.path.basename(name)
        if not safe.endswith(".ini"):
            safe += ".ini"
        return safe

    def list_configs(self, mode: str) -> List[str]:
        """Список конфигов (без расширения .ini)."""
        config_dir = CONFIGS_DIR / mode
        if not config_dir.exists():
            return []
        return sorted(
            f[:-4] for f in os.listdir(config_dir)
            if f.endswith(".ini") and os.path.isfile(config_dir / f)
        )

    def read_config(self, mode: str, name: str) -> str:
        """Читает содержимое конфига."""
        path = CONFIGS_DIR / mode / self._ensure_ini(name)
        if not path.is_file():
            raise FileNotFoundError(f"Конфиг '{name}' не найден в {mode}")
        return path.read_text(encoding="utf-8")

    def read_config_structured(self, mode: str, name: str) -> Dict:
        """Читает конфиг и возвращает структурированные данные."""
        path = CONFIGS_DIR / mode / self._ensure_ini(name)
        if not path.is_file():
            raise FileNotFoundError(f"Конфиг '{name}' не найден в {mode}")

        parser = configparser.ConfigParser(interpolation=None)
        parser.read(path, encoding="utf-8")

        sections = []
        for section in parser.sections():
            items = []
            for key, value in parser.items(section):
                items.append({"key": key, "value": value})
            sections.append({"name": section, "items": items})
        return {"name": name, "sections": sections}

    def read_config_structured_base(self) -> Dict:
        """Читает базовый конфиг (base.ini) как источник дефолтов."""
        if not KUZMICH_INI.is_file():
            raise FileNotFoundError(f"Базовый конфиг не найден: {KUZMICH_INI}")
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(KUZMICH_INI, encoding="utf-8")
        sections = []
        for section in parser.sections():
            items = []
            for key, value in parser.items(section):
                items.append({"key": key, "value": value})
            sections.append({"name": section, "items": items})
        return {"name": "base", "sections": sections}

    def save_config(self, mode: str, name: str, content: str) -> None:
        """Сохраняет конфиг."""
        config_dir = CONFIGS_DIR / mode
        config_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self._ensure_ini(name)
        path = config_dir / safe_name
        path.write_text(content, encoding="utf-8")
        log.info("Config saved: %s/%s", mode, safe_name)

    def save_config_raw(self, mode: str, name: str, content: str) -> None:
        """Сохраняет override-файл как есть (INI-текст)."""
        config_dir = CONFIGS_DIR / mode
        config_dir.mkdir(parents=True, exist_ok=True)
        safe_name = self._ensure_ini(name)
        path = config_dir / safe_name
        path.write_text(content, encoding="utf-8")
        log.info("Override saved: %s/%s", mode, safe_name)

    def delete_config(self, mode: str, name: str) -> bool:
        """Удаляет конфиг."""
        path = CONFIGS_DIR / mode / self._ensure_ini(name)
        if not path.is_file():
            return False
        # Нельзя удалить активный конфиг
        if self._active_config == name and self._active_mode == mode:
            raise ValueError("Нельзя удалить активный конфиг")
        path.unlink()
        log.info("Config deleted: %s/%s", mode, name)
        return True

    def create_config(self, mode: str, name: str) -> None:
        """Создаёт новый пустой конфиг."""
        safe_name = self._ensure_ini(name)
        config_dir = CONFIGS_DIR / mode
        config_dir.mkdir(parents=True, exist_ok=True)
        path = config_dir / safe_name
        if path.exists():
            raise ValueError(f"Файл '{name}' уже существует")
        path.write_text("", encoding="utf-8")
        log.info("Config created: %s/%s", mode, safe_name)

    def rename_config(self, mode: str, old_name: str, new_name: str) -> None:
        """Переименовывает override-файл."""
        config_dir = CONFIGS_DIR / mode
        old_path = config_dir / self._ensure_ini(old_name)
        if not old_path.is_file():
            raise FileNotFoundError(f"Конфиг '{old_name}' не найден")
        safe_new = self._ensure_ini(new_name)
        new_path = config_dir / safe_new
        if new_path.exists() and new_path != old_path:
            raise ValueError(f"Файл '{new_name}' уже существует")
        old_path.rename(new_path)
        log.info("Config renamed: %s/%s -> %s/%s", mode, old_name, mode, new_name)

    # --- Symlink management ---

    OVERRIDE_INTERNAL = VOICEROBOT_DIR / "override_internal.ini"
    OVERRIDE_EXTERNAL = VOICEROBOT_DIR / "override_external.ini"
    OVERRIDE_SYMLINK = VOICEROBOT_DIR / "override.ini"

    def _update_symlink(self, mode: str, config_name: str) -> None:
        """Создаёт/обновляет систему symlink-ов:
        - override_{mode}.ini → configs/{mode}/{name}.ini
        - override.ini → override_{mode}.ini
        """
        src = CONFIGS_DIR / mode / self._ensure_ini(config_name)
        if not src.is_file():
            raise FileNotFoundError(f"Конфиг '{config_name}' не найден в {mode}")

        # 1. override_{mode}.ini → configs/{mode}/{name}.ini
        mode_link = self.OVERRIDE_INTERNAL if mode == "internal" else self.OVERRIDE_EXTERNAL
        if mode_link.exists() or mode_link.is_symlink():
            mode_link.unlink()
        rel = os.path.relpath(src, mode_link.parent)
        os.symlink(rel, mode_link)
        log.info("Mode symlink: %s -> %s", mode_link, rel)

        # 2. override.ini → override_{mode}.ini
        if self.OVERRIDE_SYMLINK.exists() or self.OVERRIDE_SYMLINK.is_symlink():
            self.OVERRIDE_SYMLINK.unlink()
        rel_mode = os.path.relpath(mode_link, self.OVERRIDE_SYMLINK.parent)
        os.symlink(rel_mode, self.OVERRIDE_SYMLINK)
        log.info("Override symlink: %s -> %s", self.OVERRIDE_SYMLINK, rel_mode)

    # --- Process management ---

    def _start_process(self) -> bool:
        """Запускает kuzmich_companion.py: --config base.ini --config override.ini."""
        if self.is_running:
            log.info("Companion already running (pid=%s)", self.pid)
            return True

        if not COMPANION_SCRIPT.is_file():
            log.error("Companion script not found: %s", COMPANION_SCRIPT)
            return False

        cmd = [sys.executable, str(COMPANION_SCRIPT), "--config", str(KUZMICH_INI)]
        if self.OVERRIDE_SYMLINK.is_symlink():
            cmd += ["--config", str(self.OVERRIDE_SYMLINK)]

        try:
            self._process = subprocess.Popen(
                cmd,
                cwd=str(VOICEROBOT_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            log.info("Companion started (pid=%s, cmd=%s)", self._process.pid, cmd)
            return True
        except Exception as exc:
            log.error("Failed to start companion: %s", exc)
            self._process = None
            return False

    def _stop_process(self) -> bool:
        """Останавливает kuzmich_companion.py, дочерние процессы и Docker-контейнеры."""
        with self._lock:
            if self._process is None:
                return True
            if self._process.poll() is not None:
                self._process = None
                return True
            try:
                pid = self._process.pid
                # Останавливаем Docker-контейнер vllm (killpg не достанет его)
                try:
                    subprocess.run(
                        ["docker", "rm", "-f", "kuzmich_vllm"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=10, check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
                # Убиваем всю группу процессов (llama-server и т.д.)
                try:
                    os.killpg(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    try:
                        self._process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        pass
                log.info("Companion stopped (was pid=%s)", pid)
            except Exception as exc:
                log.warning("Error stopping companion: %s", exc)
            self._process = None
            return True

    def _send_sigusr1(self) -> bool:
        """Отправляет SIGUSR1 запущенному процессу."""
        pid = self.pid
        if pid is None:
            log.warning("No running companion to send SIGUSR1")
            return False
        try:
            os.kill(pid, signal.SIGUSR1)
            log.info("SIGUSR1 sent to pid=%s", pid)
            return True
        except OSError as exc:
            log.error("Failed to send SIGUSR1: %s", exc)
            return False

    # --- High-level toggle ---

    def set_mode(self, mode: str, config_name: Optional[str] = None) -> Tuple[bool, str]:
        """Переключает режим: off / internal / external.

        - off: останавливает процесс
        - internal/external: обновляет symlink, запускает или отправляет SIGUSR1
        """
        if mode not in ("off", "internal", "external"):
            return False, f"Неизвестный режим: {mode}"

        if mode == "off":
            self._stop_process()
            self._active_mode = "off"
            self._active_config = None
            return True, "Компаньон остановлен"

        # internal или external
        if config_name is None:
            # берём последний использованный или первый доступный
            config_name = self._last_config.get(mode)
            if config_name is None or not (CONFIGS_DIR / mode / self._ensure_ini(config_name)).is_file():
                configs = self.list_configs(mode)
                if not configs:
                    return False, f"Нет конфигов для режима '{mode}'"
                config_name = configs[0]

        # Проверяем что конфиг существует
        config_path = CONFIGS_DIR / mode / self._ensure_ini(config_name)
        if not config_path.is_file():
            return False, f"Конфиг '{config_name}' не найден в {mode}"

        # Обновляем symlink
        try:
            self._update_symlink(mode, config_name)
        except Exception as exc:
            return False, f"Ошибка создания symlink: {exc}"

        # Если процесс уже запущен и конфиг изменился — SIGUSR1
        if self.is_running:
            if self._active_mode == mode and self._active_config == config_name:
                return True, f"Компаньон уже работает в режиме '{mode}' с конфигом '{config_name}'"
            self._send_sigusr1()
            self._active_mode = mode
            self._active_config = config_name
            self._last_config[mode] = config_name
            return True, f"Режим '{mode}', конфиг '{config_name}' — перезагружен (SIGUSR1)"

        # Запускаем новый процесс
        if not self._start_process():
            return False, "Не удалось запустить kuzmich_companion.py"

        self._active_mode = mode
        self._active_config = config_name
        self._last_config[mode] = config_name
        return True, f"Компаньон запущен в режиме '{mode}' с конфигом '{config_name}'"

    def switch_config(self, config_name: str) -> Tuple[bool, str]:
        """Меняет конфиг в текущем активном режиме (только internal/external)."""
        if self._active_mode == "off":
            return False, "Компаньон выключен"
        return self.set_mode(self._active_mode, config_name)

    def select_config(self, mode: str, config_name: str) -> Tuple[bool, str]:
        """Выбирает конфиг для режима. Обновляет symlink и _last_config.

        Если компаньон запущен в ЭТОМ же режиме — перезапускает его
        с новым symlink. Иначе только обновляет per-mode symlink.
        """
        if mode not in ("internal", "external"):
            return False, f"Неизвестный режим: {mode}"

        # Проверяем что конфиг существует
        config_path = CONFIGS_DIR / mode / self._ensure_ini(config_name)
        if not config_path.is_file():
            return False, f"Конфиг '{config_name}' не найден в {mode}"

        # Обновляем per-mode symlink и _last_config
        try:
            self._update_symlink(mode, config_name)
        except Exception as exc:
            return False, f"Ошибка создания symlink: {exc}"
        self._last_config[mode] = config_name

        # Если компаньон запущен в этом режиме — SIGUSR1 для перечитывания symlink
        if self.is_running and self._active_mode == mode:
            if self._active_config == config_name:
                return True, f"Конфиг '{config_name}' уже активен для '{mode}'"
            self._active_config = config_name
            self._send_sigusr1()
            return True, f"Режим '{mode}', конфиг '{config_name}' — перезагружен"

        return True, f"Конфиг '{config_name}' выбран для '{mode}'"


COMPANION = CompanionManager()
