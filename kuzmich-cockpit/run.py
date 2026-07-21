"""Запуск пульта одной командой: python run.py

Читает COCKPIT_HOST/COCKPIT_PORT/COCKPIT_DRY_RUN из окружения (см. backend/config.py).
По умолчанию dry-run (без робота) на 0.0.0.0:8080.
"""
import uvicorn

from backend.config import CONFIG

if __name__ == "__main__":
    uvicorn.run("backend.app:app", host=CONFIG.host, port=CONFIG.port, reload=False)
