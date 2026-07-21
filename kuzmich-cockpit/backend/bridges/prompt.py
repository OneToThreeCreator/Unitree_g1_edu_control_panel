"""Управление системными промтами ИИ.

Промты хранятся в виде текстовых файлов в директории, указанной в CONFIG.prompts_dir.
Активный промт — это символическая ссылка по пути CONFIG.active_prompt_link.
"""
import os
import logging
from typing import List

from backend.config import CONFIG

logger = logging.getLogger(__name__)


def _ensure_dir(path: str) -> None:
    """Создаёт директорию, если её нет."""
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
        logger.info(f"Создана директория для промтов: {path}")


def list_prompts() -> List[str]:
    """Возвращает список имён файлов промтов (без пути)."""
    _ensure_dir(CONFIG.prompts_dir)
    files = []
    for f in os.listdir(CONFIG.prompts_dir):
        full = os.path.join(CONFIG.prompts_dir, f)
        if os.path.isfile(full):
            files.append(f)
    return sorted(files)


def read_prompt(name: str) -> str:
    """Возвращает содержимое промта по имени файла."""
    _ensure_dir(CONFIG.prompts_dir)
    path = os.path.join(CONFIG.prompts_dir, name)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Промт '{name}' не найден")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def write_prompt(name: str, content: str) -> None:
    """Записывает содержимое в файл промта (создаёт или перезаписывает)."""
    _ensure_dir(CONFIG.prompts_dir)
    safe_name = os.path.basename(name)
    if safe_name != name:
        raise ValueError("Имя файла содержит недопустимые символы пути")
    path = os.path.join(CONFIG.prompts_dir, safe_name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"Промт '{name}' сохранён")


def delete_prompt(name: str) -> bool:
    """Удаляет файл промта. Возвращает True, если удалён, иначе False."""
    _ensure_dir(CONFIG.prompts_dir)
    path = os.path.join(CONFIG.prompts_dir, name)
    if not os.path.isfile(path):
        return False
    os.remove(path)
    logger.info(f"Промт '{name}' удалён")
    return True


def select_prompt(name: str) -> bool:
    """Делает промт активным, создавая символическую ссылку на него.

    Возвращает True в случае успеха.
    """
    _ensure_dir(CONFIG.prompts_dir)
    src = os.path.join(CONFIG.prompts_dir, name)
    if not os.path.isfile(src):
        raise FileNotFoundError(f"Промт '{name}' не найден")

    link = CONFIG.active_prompt_link
    if os.path.lexists(link):
        os.unlink(link)

    try:
        rel_src = os.path.relpath(src, os.path.dirname(link) or ".")
        os.symlink(rel_src, link)
        logger.info(f"Активный промт установлен: {link} -> {src}")
        return True
    except OSError as e:
        logger.error(f"Не удалось создать ссылку: {e}")
        return False

def rename_prompt(old_name: str, new_name: str) -> None:
    _ensure_dir(CONFIG.prompts_dir)
    old_path = os.path.join(CONFIG.prompts_dir, old_name)
    if not os.path.isfile(old_path):
        raise FileNotFoundError(f"Промт '{old_name}' не найден")
    safe_new = os.path.basename(new_name)
    if safe_new != new_name:
        raise ValueError("Имя файла содержит недопустимые символы пути")
    new_path = os.path.join(CONFIG.prompts_dir, safe_new)
    if os.path.exists(new_path):
        raise ValueError(f"Промт с именем '{new_name}' уже существует")
    os.rename(old_path, new_path)
    # Обновление активной ссылки, если она указывала на старый файл
    link = CONFIG.active_prompt_link
    if os.path.lexists(link):
        try:
            target = os.readlink(link)
            if os.path.abspath(os.path.join(os.path.dirname(link), target)) == os.path.abspath(old_path):
                os.unlink(link)
                rel_src = os.path.relpath(new_path, os.path.dirname(link) or ".")
                os.symlink(rel_src, link)
        except OSError:
            pass
    logger.info(f"Промт переименован: {old_name} -> {new_name}")
