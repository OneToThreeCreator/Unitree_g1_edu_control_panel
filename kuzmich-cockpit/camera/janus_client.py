"""Async Janus Gateway client using HTTP transport with proper event polling.

Janus Streaming plugin uses async events:
1. watch → ack + async event with SDP offer
2. start with JSEP answer → ack + async "starting" event

We poll /janus/{session_id} to receive async events.
"""
from __future__ import annotations

import asyncio
import logging
import time
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
    """Async client for Janus Gateway Streaming plugin via HTTP."""

    def __init__(self, base_url: str = "http://127.0.0.1:8088") -> None:
        self._base_url = base_url.rstrip("/")
        self._session_id: Optional[int] = None
        self._handle_id: Optional[int] = None
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def session_id(self) -> Optional[int]:
        return self._session_id

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def _transaction(self) -> str:
        return uuid.uuid4().hex[:12]

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
        return self._session

    async def _send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Send message and return response."""
        session = await self._get_session()
        txn = payload.get("transaction", self._transaction())
        payload["transaction"] = txn

        # Determine URL based on context
        if self._session_id and self._handle_id:
            url = f"{self._base_url}/janus/{self._session_id}/{self._handle_id}"
        elif self._session_id:
            url = f"{self._base_url}/janus/{self._session_id}"
        else:
            url = f"{self._base_url}/janus"

        async with session.post(url, json=payload) as resp:
            data = await resp.json()
            if data.get("janus") == "error":
                err = data.get("error", {})
                raise JanusError(err.get("code", -1), err.get("reason", "unknown"))
            return data

    async def _poll_events(self, timeout: float = 5) -> list:
        """Poll Janus long-poll endpoint for async events."""
        if not self._session_id:
            return []
        session = await self._get_session()
        url = f"{self._base_url}/janus/{self._session_id}"
        events = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                async with session.get(url) as resp:
                    data = await resp.json()
                    if data.get("janus") == "event":
                        events.append(data)
                        return events
                    elif data.get("janus") == "error":
                        log.warning("Janus poll error: %s", data)
                        return events
            except asyncio.TimeoutError:
                break
            except Exception:
                break
            await asyncio.sleep(0.05)
        return events

    async def create_session(self) -> int:
        result = await self._send({"janus": "create"})
        self._session_id = result["data"]["id"]
        log.info("Janus session created: %s", self._session_id)
        return self._session_id

    async def attach_plugin(self, plugin: str = "janus.plugin.streaming") -> int:
        if not self._session_id:
            raise JanusError(-1, "No session")

        result = await self._send({
            "janus": "attach",
            "plugin": plugin,
        })
        self._handle_id = result["data"]["id"]
        log.info("Attached to %s: handle=%s", plugin, self._handle_id)
        return self._handle_id

    async def watch(self, mountpoint_id: int = 1) -> Dict[str, Any]:
        """Watch mountpoint. Returns SDP offer from async event."""
        # Send watch request (synchronous ack)
        await self._send({
            "janus": "message",
            "body": {"request": "watch", "id": mountpoint_id},
        })
        log.info("Watch sent, polling for SDP offer event...")

        # Poll for async event with SDP offer
        events = await self._poll_events(timeout=10)
        for event in events:
            jsep = event.get("jsep")
            if jsep and jsep.get("sdp"):
                log.info("Got SDP offer from Janus")
                return event

        raise JanusError(-1, "No SDP offer received from Janus")

    async def start(self, sdp: str) -> Dict[str, Any]:
        """Start stream with SDP answer in JSEP. Returns ack immediately."""
        result = await self._send({
            "janus": "message",
            "body": {"request": "start"},
            "jsep": {"type": "answer", "sdp": sdp},
        })
        log.info("Start sent to Janus")
        return result

    async def trickle_ice(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """Send ICE candidate."""
        result = await self._send({
            "janus": "trickle",
            "candidate": candidate,
        })
        log.info("Trickle sent: %s", result.get("janus"))
        return result

    async def destroy_session(self) -> None:
        if not self._session_id:
            return
        try:
            await self._send({"janus": "destroy"})
        except Exception:
            pass
        self._session_id = None
        self._handle_id = None
