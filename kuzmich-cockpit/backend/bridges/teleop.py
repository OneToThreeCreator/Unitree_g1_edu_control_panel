"""Мостик управления Treelogic Teleop.

Управляет запуском/остановкой teleop_bridge через Treelogic REST API,
мониторинг статуса,获取 camera preview state.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

log = logging.getLogger("cockpit.teleop")


class TeleopBridge:
    """HTTP client for Treelogic Teleop REST API."""

    def __init__(self, base_url: str = "http://192.168.1.102", timeout: float = 5.0) -> None:
        self._base_url = base_url
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        return self._base_url

    @base_url.setter
    def base_url(self, url: str) -> None:
        self._base_url = url.rstrip("/")

    def status(self) -> Dict[str, Any]:
        """Get teleop_bridge service status (sync)."""
        try:
            resp = httpx.get(f"{self._base_url}/api/services", timeout=self._timeout)
            if resp.status_code == 200:
                data = resp.json()
                for svc in data.get("services", []):
                    if svc.get("name") == "teleop_bridge":
                        return {
                            "running": svc.get("running", False),
                            "pid": svc.get("pid"),
                            "label": svc.get("label", "teleop_bridge"),
                        }
        except httpx.HTTPError:
            pass
        return {"running": False, "pid": None, "label": "teleop_bridge"}

    async def async_status(self) -> Dict[str, Any]:
        """Get teleop_bridge service status (async)."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{self._base_url}/api/services")
                if resp.status_code == 200:
                    data = resp.json()
                    for svc in data.get("services", []):
                        if svc.get("name") == "teleop_bridge":
                            return {
                                "running": svc.get("running", False),
                                "pid": svc.get("pid"),
                                "label": svc.get("label", "teleop_bridge"),
                            }
        except httpx.HTTPError:
            pass
        return {"running": False, "pid": None, "label": "teleop_bridge"}

    async def is_running(self) -> bool:
        """Check if teleop_bridge is running."""
        st = await self.async_status()
        return st.get("running", False)

    async def is_preview_active(self) -> bool:
        """Check if Teleop camera preview is active."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{self._base_url}/api/camera/preview/status")
                if resp.status_code == 200:
                    return resp.json().get("active", False)
        except httpx.HTTPError:
            pass
        return False

    async def start(self) -> bool:
        """Start teleop_bridge service."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base_url}/api/services/teleop_bridge/start")
                if resp.status_code == 200:
                    log.info("Teleop started")
                    return True
        except httpx.HTTPError as e:
            log.warning("Failed to start Teleop: %s", e)
        return False

    async def stop(self) -> bool:
        """Stop teleop_bridge service."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base_url}/api/services/teleop_bridge/stop")
                if resp.status_code == 200:
                    log.info("Teleop stopped")
                    return True
        except httpx.HTTPError as e:
            log.warning("Failed to stop Teleop: %s", e)
        return False

    async def get_camera_config(self) -> Dict[str, Any]:
        """Get current camera configuration from Teleop."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{self._base_url}/api/camera/config")
                if resp.status_code == 200:
                    return resp.json()
        except httpx.HTTPError:
            pass
        return {}


# Singleton
TELEOP = TeleopBridge()
