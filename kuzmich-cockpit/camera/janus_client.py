"""Async Janus Gateway client for VideoRoom plugin.

Handles session/attach/join/publish operations via Janus HTTP long-poll API.
Used to bridge GStreamer RTP output to browser WebRTC viewers.
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
    """Async client for Janus Gateway VideoRoom plugin.

    Usage:
        client = JanusClient("http://127.0.0.1:8088")
        await client.create_session()
        await client.attach_plugin()
        await client.join_room(room_id, publisher=True)
        await client.configure_publisher(sdp)
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8088") -> None:
        self._base_url = base_url.rstrip("/")
        self._session_id: Optional[int] = None
        self._handle_id: Optional[int] = None
        self._plugin: str = "janus.plugin.videoroom"
        self._pending: Dict[str, asyncio.Future] = {}
        self._poll_task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def session_id(self) -> Optional[int]:
        return self._session_id

    @property
    def handle_id(self) -> Optional[int]:
        return self._handle_id

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def close(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _transaction(self) -> str:
        return uuid.uuid4().hex[:12]

    async def _send(self, body: Dict[str, Any], handle: bool = False) -> Dict[str, Any]:
        """Send message to Janus and wait for response."""
        txn = self._transaction()
        session = await self._get_session()

        payload: Dict[str, Any] = {"transaction": txn}
        if handle and self._handle_id:
            payload["handle_id"] = self._handle_id
        elif self._session_id and not handle:
            payload["session_id"] = self._session_id
        payload["body"] = body

        # Create future for this transaction
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[txn] = fut

        try:
            url = f"{self._base_url}/janus"
            async with session.post(url, json=payload) as resp:
                data = await resp.json()
                if data.get("janus") == "error":
                    err = data.get("error", {})
                    raise JanusError(err.get("code", -1), err.get("reason", "unknown"))
                return data
        finally:
            self._pending.pop(txn, None)

    async def create_session(self) -> int:
        """Create Janus session."""
        session = await self._get_session()
        txn = self._transaction()

        payload = {"transaction": txn, "janus": "create", "apisecret": None}
        url = f"{self._base_url}/janus"
        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            if data.get("janus") == "error":
                err = data.get("error", {})
                raise JanusError(err.get("code", -1), err.get("reason", "unknown"))
            self._session_id = data["data"]["id"]
            log.info("Janus session created: %s", self._session_id)
            return self._session_id

    async def attach_plugin(self, plugin: str = "janus.plugin.videoroom") -> int:
        """Attach to Janus plugin."""
        if not self._session_id:
            raise JanusError(-1, "No session — call create_session first")

        self._plugin = plugin
        body = {"plugin": plugin}
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

    async def join_room(self, room_id: int, publisher: bool = True) -> Dict[str, Any]:
        """Join VideoRoom as publisher or subscriber."""
        if not self._handle_id:
            raise JanusError(-1, "No handle — call attach_plugin first")

        body: Dict[str, Any] = {
            "request": "join",
            "room": room_id,
            "ptype": "publisher" if publisher else "subscriber",
            "display": "unitree-g1",
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
            if data.get("janus") == "error":
                err = data.get("error", {})
                raise JanusError(err.get("code", -1), err.get("reason", "unknown"))
            log.info("Joined room %s (publisher=%s)", room_id, publisher)
            return data

    async def configure_publisher(
        self,
        audio: bool = False,
        video: bool = True,
        data: bool = False,
    ) -> Dict[str, Any]:
        """Configure publisher capabilities."""
        if not self._handle_id:
            raise JanusError(-1, "No handle — call attach_plugin first")

        body: Dict[str, Any] = {
            "request": "configure",
            "audio": audio,
            "video": video,
            "data": data,
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
            if data.get("janus") == "error":
                err = data.get("error", {})
                raise JanusError(err.get("code", -1), err.get("reason", "unknown"))
            log.info("Publisher configured")
            return data

    async def publish(self, sdp: str, room_id: int, bitrates: Optional[Dict] = None) -> Dict[str, Any]:
        """Publish SDP offer from GStreamer to VideoRoom.

        This sends the RTP stream from GStreamer to Janus.
        The SDP should contain the RTP media description.
        """
        if not self._handle_id:
            raise JanusError(-1, "No handle — call attach_plugin first")

        body: Dict[str, Any] = {
            "request": "publish",
            "audio": False,
            "video": True,
            "data": False,
        }
        if bitrates:
            body.update(bitrates)

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
            log.info("Published to room %s", room_id)
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

    async def destroy_room(self, room_id: int) -> Dict[str, Any]:
        """Destroy VideoRoom."""
        if not self._handle_id:
            raise JanusError(-1, "No handle — call attach_plugin first")

        body: Dict[str, Any] = {
            "request": "destroy",
            "room": room_id,
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
