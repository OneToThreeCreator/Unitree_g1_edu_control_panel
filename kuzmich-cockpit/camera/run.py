"""Independent camera video server for Kuzmich Cockpit.

Runs as a standalone process on a separate port (default 8081).
No dependency on the main cockpit backend — only camera module + config.

Usage (from kuzmich-cockpit/):
    python -m camera.run
    CAMERA_PORT=8081 python -m camera.run
"""
from __future__ import annotations

import logging
import os

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from camera.config import CameraConfig
from camera import router, init_camera, get_camera_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("camera-server")

camera_app = FastAPI(title="Kuzmich Camera Server")
camera_app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
camera_app.include_router(router)


@camera_app.on_event("startup")
async def _startup() -> None:
    cfg = CameraConfig()
    init_camera(cfg)
    cam = get_camera_manager()
    if cam:
        try:
            await cam.start()
        except Exception as e:
            log.warning("Camera auto-start failed: %s", e)
    log.info("Camera server up on %s:%s", _host, _port)


@camera_app.on_event("shutdown")
async def _shutdown() -> None:
    cam = get_camera_manager()
    if cam:
        await cam.shutdown()


_host = os.environ.get("CAMERA_HOST", "0.0.0.0")
_port = int(os.environ.get("CAMERA_PORT", "8081"))

if __name__ == "__main__":
    uvicorn.run(
        "camera.run:camera_app",
        host=_host,
        port=_port,
        reload=False,
    )
