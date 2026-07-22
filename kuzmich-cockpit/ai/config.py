"""INI config loader with SIGUSR1 hot-reload.

Supports:
  - Duplicate keys within a section (collected into lists via ``getlist``)
  - Multiple config files (later files override earlier ones)
  - Hot-reload on SIGUSR1
"""
from __future__ import annotations

import configparser
import logging
import os
import re
import signal
import threading
from pathlib import Path
from typing import Callable, Optional


def _parse_all_values(ini_text: str) -> dict[str, dict[str, list[str]]]:
    """Parse INI text, collecting ALL values per key (including duplicates).

    Returns ``{section: {key: [val1, val2, ...]}}``.
    """
    result: dict[str, dict[str, list[str]]] = {}
    current_section = ""
    current_key = ""
    current_values: list[str] = []
    section_re = re.compile(r"^\[([^\]]+)\]\s*$")
    kv_re = re.compile(r"^([^=]+)=(.*)$")

    def _flush() -> None:
        nonlocal current_key, current_values
        if current_section and current_key and current_values:
            result.setdefault(current_section, {}).setdefault(current_key, []).extend(current_values)
        current_key = ""
        current_values = []

    for line in ini_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped[0] in "#;":
            continue
        m = section_re.match(stripped)
        if m:
            _flush()
            current_section = m.group(1).strip()
            continue
        m = kv_re.match(stripped)
        if m and current_section:
            _flush()
            current_key = m.group(1).strip()
            current_values = [m.group(2).strip()]
        elif m is None and current_key:
            # continuation line (shouldn't happen in normal INI, but be safe)
            pass

    _flush()
    return result


class Config:
    """Thread-safe INI config with hot-reload via SIGUSR1.

    Supports multiple config files and duplicate keys.

    Usage::

        cfg = Config()
        cfg.load("/path/to/base.ini")
        cfg.load("/path/to/override.ini")   # overrides base
        val = cfg.get("ai", "base_url")
        vals = cfg.getlist("llama", "extra_args")  # all values
        cfg.on_reload(my_callback)
        cfg.setup_reload_signal()
    """

    def __init__(self) -> None:
        self._parser = configparser.ConfigParser(interpolation=None, strict=False)
        self._all_values: dict[str, dict[str, list[str]]] = {}
        self._paths: list[Path] = []
        self._lock = threading.RLock()  # RLock: reentrant, safe if SIGUSR1 arrives during reload()
        self._on_reload: list[Callable[[], None]] = []

    # -- loading / reloading ---------------------------------------------------

    def load(self, *paths: str | Path) -> None:
        """Load one or more INI files.  Later files override earlier ones."""
        resolved = [Path(p).expanduser().resolve() for p in paths]
        with self._lock:
            for p in resolved:
                self._parser.read(p, encoding="utf-8")
                # Merge all-values for getlist()
                text = p.read_text(encoding="utf-8")
                parsed = _parse_all_values(text)
                for section, keys in parsed.items():
                    sec = self._all_values.setdefault(section, {})
                    for key, values in keys.items():
                        sec[key] = values  # later file replaces (not appends)
                if p not in self._paths:
                    self._paths.append(p)
        if resolved:
            os.chdir(resolved[-1].parent)
            logging.info("Config loaded from %s (cwd=%s)", resolved, resolved[-1].parent)

    def reload(self) -> bool:
        if not self._paths:
            return False
        with self._lock:
            self._parser = configparser.ConfigParser(interpolation=None, strict=False)
            self._all_values.clear()
            for p in self._paths:
                self._parser.read(p, encoding="utf-8")
                text = p.read_text(encoding="utf-8")
                parsed = _parse_all_values(text)
                for section, keys in parsed.items():
                    sec = self._all_values.setdefault(section, {})
                    for key, values in keys.items():
                        sec[key] = values
        logging.info("Config reloaded from %s", self._paths)
        for cb in self._on_reload:
            try:
                cb()
            except Exception:
                logging.exception("on_reload callback failed")
        return True

    def on_reload(self, callback: Callable[[], None]) -> None:
        """Register a callback to run after each hot-reload."""
        self._on_reload.append(callback)

    def setup_reload_signal(self) -> None:
        """Register SIGUSR1 handler that re-reads the INI file."""
        def _handler(signum: int, frame: object) -> None:
            self.reload()
        signal.signal(signal.SIGUSR1, _handler)

    # -- typed getters ---------------------------------------------------------

    def get(self, section: str, key: str, fallback: str = "") -> str:
        with self._lock:
            return self._parser.get(section, key, fallback=fallback)

    def getlist(self, section: str, key: str) -> list[str]:
        """Return all values for *key* in *section* (duplicate keys).

        When a key appears multiple times, ``get()`` returns the last value
        while ``getlist()`` returns all of them in file order.
        """
        with self._lock:
            return list(self._all_values.get(section, {}).get(key, []))

    def getint(self, section: str, key: str, fallback: int = 0) -> int:
        with self._lock:
            return self._parser.getint(section, key, fallback=fallback)

    def getfloat(self, section: str, key: str, fallback: float = 0.0) -> float:
        with self._lock:
            return self._parser.getfloat(section, key, fallback=fallback)

    def getboolean(self, section: str, key: str, fallback: bool = False) -> bool:
        with self._lock:
            return self._parser.getboolean(section, key, fallback=fallback)

    def has_section(self, section: str) -> bool:
        with self._lock:
            return self._parser.has_section(section)

    def path_str(self) -> str:
        return str(self._paths[-1]) if self._paths else ""

    # -- convenience: expand user paths & env vars -----------------------------

    def getpath(self, section: str, key: str, fallback: str = "") -> str:
        """Return value with ~ expanded and $VAR references resolved."""
        raw = self.get(section, key, fallback=fallback)
        return os.path.expanduser(os.path.expandvars(raw))
