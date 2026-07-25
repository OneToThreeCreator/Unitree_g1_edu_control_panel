"""Async Janus Gateway client using WebSocket transport.

Flow for Streaming plugin:
1. watch → Janus returns SDP offer in async event
2. start with JSEP answer → Janus starts relaying
3. trickle ICE candidates separately
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, Optional

import aiohttp

log = logging.getLogger("cockpit.camera.janus")


class JanusError(Exception):
    def __init__(self, code: int = 0, reason: str = ""):
        self.code = code
        self.reason = reason
        super().__init__(f"Janus error {code}: {reason}")


class JanusClient:
    """Async client for Janus Gateway Streaming plugin via WebSocket."""

    def __init__(self, ws_url: str = "ws://127.0.0.1:8188/janus") -> None:
        self._ws_url = ws_url
        self._session_id: Optional[int] = None
        self._handle_id: Optional[int] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._pending_events: asyncio.Queue = asyncio.Queue()

    @property
    def session_id(self) -> Optional[int]:
        return self._session_id

    async def close(self) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
        self._ws = None
        self._session = None

    def _transaction(self) -> str:
        return uuid.uuid4().hex[:12]

    async def connect(self) -> None:
        """Connect to Janus via WebSocket."""
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._ws_url)
        log.info("Connected to Janus WebSocket: %s", self._ws_url)

    async def _send_and_wait(self, payload: Dict[str, Any], timeout: float = 10) -> Dict[str, Any]:
        """Send message and wait for response matching transaction."""
        if not self._ws or self._ws.closed:
            raise JanusError(-1, "Not connected to Janus")

        txn = payload.get("transaction", self._transaction())
        payload["transaction"] = txn

        await self._ws.send_json(payload)

        # Wait for response with matching transaction
        import time
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(self._ws.receive(), timeout=1.0)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("transaction") == txn:
                        if data.get("janus") == "error":
                            err = data.get("error", {})
                            raise JanusError(err.get("code", -1), err.get("reason", "unknown"))
                        return data
                    # Store other messages (events) for later pickup
                    await self._pending_events.put(data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    raise JanusError(-1, "WebSocket closed")
            except asyncio.TimeoutError:
                continue

        raise JanusError(-1, f"Timeout waiting for response to {payload.get('janus')}")

    async def _poll_event(self, timeout: float = 5) -> Optional[Dict[str, Any]]:
        """Poll for async events from Janus."""
        import time
        deadline = time.time() + timeout

        # First check queued events
        while not self._pending_events.empty():
            event = self._pending_events.get_nowait()
            if event.get("janus") == "event":
                return event

        # Then wait for new messages
        while time.time() < deadline:
            try:
                msg = await asyncio.wait_for(self._ws.receive(), timeout=1.0)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("janus") == "event":
                        return data
                    # Queue non-event messages
                    await self._pending_events.put(data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    return None
            except asyncio.TimeoutError:
                continue

        return None

    async def create_session(self) -> int:
        result = await self._send_and_wait({"janus": "create"})
        self._session_id = result["data"]["id"]
        log.info("Janus session created: %s", self._session_id)
        return self._session_id

    async def attach_plugin(self, plugin: str = "janus.plugin.streaming") -> int:
        if not self._session_id:
            raise JanusError(-1, "No session")

        result = await self._send_and_wait({
            "janus": "attach",
            "session_id": self._session_id,
            "plugin": plugin,
        })
        self._handle_id = result["data"]["id"]
        log.info("Attached to %s: handle=%s", plugin, self._handle_id)
        return self._handle_id

    async def watch(self, mountpoint_id: int = 1) -> Dict[str, Any]:
        """Watch a mountpoint. Returns SDP offer in async event."""
        # Send watch
        result = await self._send_and_wait({
            "janus": "message",
            "session_id": self._session_id,
            "handle_id": self._handle_id,
            "body": {"request": "watch", "id": mountpoint_id},
        })
        log.info("Watch sent, waiting for SDP offer event...")

        # Wait for async event with SDP offer
        event = await self._poll_event(timeout=10)
        if not event:
            raise JanusError(-1, "No event received after watch")

        jsep = event.get("jsep")
        if not jsep or not jsep.get("sdp"):
            raise JanusError(-1, f"No SDP in event: {event}")

        log.info("Got SDP offer from Janus")
        return event

    async def start(self, sdp: str) -> Dict[str, Any]:
        """Start stream with SDP answer in JSEP."""
        result = await self._send_and_wait({
            "janus": "message",
            "session_id": self._session_id,
            "handle_id": self._handle_id,
            "body": {"request": "start"},
            "jsep": {"type": "answer", "sdp": sdp},
        })
        log.info("Start sent, waiting for starting event...")

        # Wait for async "starting" event
        event = await self._poll_event(timeout=5)
        if event:
            status = event.get("plugindata", {}).get("data", {}).get("status")
            log.info("Stream status: %s", status)

        return result

    async def trickle_ice(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """Send ICE candidate."""
        return await self._send_and_wait({
            "janus": "trickle",
            "session_id": self._session_id,
            "handle_id": self._handle_id,
            "candidate": candidate,
        }, timeout=3)

    async def destroy_session(self) -> None:
        if not self._session_id:
            return
        try:
            await self._send_and_wait({
                "janus": "destroy",
                "session_id": self._session_id,
            }, timeout=3)
        except Exception:
            pass
        self._session_id = None
        self._handle_id = None
