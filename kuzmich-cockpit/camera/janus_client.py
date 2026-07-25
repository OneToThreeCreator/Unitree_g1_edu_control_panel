"""Async Janus Gateway client for Streaming plugin.

Handles session/attach/watch operations via Janus HTTP long-poll API.
Used to bridge GStreamer RTP output to browser WebRTC viewers.

Flow:
1. GStreamer sends RTP to Janus on configured ports
2. Browser creates RTCPeerConnection, sends SDP offer
3. Backend forwards offer to Janus via Streaming plugin "watch"
4. Janus returns SDP answer, backend returns to browser
5. WebRTC stream flows from Janus to browser
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional

import aiohttp

log = logging.getLogger("cockpit.camera.janus")


class JanusError(Exception):
    """Janus API error."""

    def __init__(self, code: int = 0, reason: str = ""):
        self.code = code
        self.reason = reason
        super().__init__(f"Janus error {code}: {reason}")


class JanusClient:
    """Async client for Janus Gateway Streaming plugin."""

    def __init__(self, base_url: str = "http://127.0.0.1:8088") -> None:
        self._base_url = base_url.rstrip("/")
        self._session_id: Optional[int] = None
        self._handle_id: Optional[int] = None
        self._plugin: str = "janus.plugin.streaming"
        self._session: Optional[aiohttp.ClientSession] = None
        self._mountpoint_id: int = 1  # Default mountpoint

    @property
    def session_id(self) -> Optional[int]:
        return self._session_id

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _transaction(self) -> str:
        return uuid.uuid4().hex[:12]

    async def create_session(self) -> int:
        """Create Janus session."""
        session = await self._get_session()
        txn = self._transaction()

        payload = {"transaction": txn, "janus": "create"}
        url = f"{self._base_url}/janus"
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            if data.get("janus") == "error":
                err = data.get("error", {})
                raise JanusError(err.get("code", -1), err.get("reason", "unknown"))
            self._session_id = data["data"]["id"]
            log.info("Janus session created: %s", self._session_id)
            return self._session_id

    async def attach_plugin(self, plugin: str = "janus.plugin.streaming") -> int:
        """Attach to Janus plugin."""
        if not self._session_id:
            raise JanusError(-1, "No session — call create_session first")

        self._plugin = plugin
        session = await self._get_session()
        txn = self._transaction()

        payload = {
            "transaction": txn,
            "session_id": self._session_id,
            "janus": "attach",
            "plugin": plugin,
        }
        url = f"{self._base_url}/janus/{self._session_id}"
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            if data.get("janus") == "error":
                err = data.get("error", {})
                raise JanusError(err.get("code", -1), err.get("reason", "unknown"))
            self._handle_id = data["data"]["id"]
            log.info("Janus plugin attached: handle=%s", self._handle_id)
            return self._handle_id

    async def watch(self, mountpoint_id: int = 1, sdp: str = "") -> Dict[str, Any]:
        """Watch a streaming mountpoint (subscriber flow).

        Sends SDP offer to Janus, receives SDP answer.
        """
        if not self._handle_id:
            raise JanusError(-1, "No handle — call attach_plugin first")

        body: Dict[str, Any] = {
            "request": "watch",
            "id": mountpoint_id,
        }
        # If SDP provided, include it for trickle ICE
        if sdp:
            body["offer"] = {"type": "offer", "sdp": sdp}

        session = await self._get_session()
        txn = self._transaction()

        payload = {
            "transaction": txn,
            "session_id": self._session_id,
            "handle_id": self._handle_id,
            "janus": "message",
            "body": body,
        }
        url = f"{self._base_url}/janus/{self._session_id}/{self._handle_id}"
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            if data.get("janus") == "error":
                err = data.get("error", {})
                raise JanusError(err.get("code", -1), err.get("reason", "unknown"))
            return data

    async def start(self, mountpoint_id: int = 1) -> Dict[str, Any]:
        """Start receiving media after watch."""
        if not self._handle_id:
            raise JanusError(-1, "No handle — call attach_plugin first")

        body: Dict[str, Any] = {
            "request": "start",
        }

        session = await self._get_session()
        txn = self._transaction()

        payload = {
            "transaction": txn,
            "session_id": self._session_id,
            "handle_id": self._handle_id,
            "janus": "message",
            "body": body,
        }
        url = f"{self._base_url}/janus/{self._session_id}/{self._handle_id}"
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            return data

    async def trickle_ice(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """Send ICE candidate to Janus."""
        if not self._handle_id:
            raise JanusError(-1, "No handle — call attach_plugin first")

        session = await self._get_session()
        txn = self._transaction()

        payload = {
            "transaction": txn,
            "session_id": self._session_id,
            "handle_id": self._handle_id,
            "janus": "trickle",
            "candidate": candidate,
        }
        url = f"{self._base_url}/janus/{self._session_id}/{self._handle_id}"
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            return data

    async def destroy_session(self) -> None:
        """Destroy Janus session."""
        if not self._session_id:
            return
        session = await self._get_session()
        txn = self._transaction()
        payload = {
            "transaction": txn,
            "janus": "destroy",
            "session_id": self._session_id,
        }
        url = f"{self._base_url}/janus"
        try:
            await session.post(url, json=payload)
        except Exception:
            pass
        self._session_id = None
        self._handle_id = None
