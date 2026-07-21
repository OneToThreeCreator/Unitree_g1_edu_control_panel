"""Мостик видео: прокси MJPEG-потока от see_for_robot_v3 (порт 8091).

v1 — просто проксируем уже работающий MJPEG (RealSense + YOLO-оверлей) на пульт,
чтобы видео было в одном окне со всем управлением. Низколатентный WebRTC — v2.
В dry-run реального стрима нет: возвращаем 503, фронтенд показывает заглушку.
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

import httpx
from fastapi import APIRouter, Response
from fastapi.responses import StreamingResponse

from ..config import CONFIG

log = logging.getLogger("cockpit.video")

router = APIRouter(prefix="/video", tags=["video"])


@router.get("/snapshot.jpg")
async def snapshot() -> Response:
    if CONFIG.dry_run:
        return Response(status_code=503, content=b"no video in dry-run")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{CONFIG.video_mjpeg_url}/snapshot.jpg")
        return Response(content=resp.content, media_type="image/jpeg",
                        headers={"Cache-Control": "no-store"})
    except httpx.HTTPError as exc:
        log.warning("snapshot proxy failed: %s", exc)
        return Response(status_code=502, content=str(exc).encode())


@router.get("/stream.mjpg")
async def stream() -> Response:
    if CONFIG.dry_run:
        return Response(status_code=503, content=b"no video in dry-run")

    async def gen() -> AsyncIterator[bytes]:
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("GET", f"{CONFIG.video_mjpeg_url}/stream.mjpg") as resp:
                    async for chunk in resp.aiter_bytes():
                        yield chunk
        except httpx.HTTPError as exc:
            log.warning("video stream failed: %s", exc)
        except OSError as exc:
            log.warning("video stream connection failed: %s", exc)

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")
