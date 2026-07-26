"""WHIP publisher — reads H.265 from GStreamer WebSocket, publishes via webrtcbin to LiveKit.

Architecture:
    Main pipeline (gst-launch-1.0):
        appsrc → encode → tee → [websocketsink:8084 (H.265), websocketsink:8082 (MJPEG)]

    This publisher:
        websocketclientsrc:8084 (H.265) → h265parse → webrtcbin → LiveKit WHIP

    Python builds a separate GStreamer pipeline with webrtcbin and handles
    WHIP signaling (SDP offer/answer + ICE candidates) via HTTP.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

import aiohttp
import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")
from gi.repository import GLib, Gst, GstSdp, GstWebRTC

log = logging.getLogger("cockpit.camera.whip")

Gst.init(None)


class WHIPPublisher:
    """Publishes H.265 video to LiveKit via WHIP using webrtcbin.

    Reads encoded H.265 from GStreamer's websocketsink (port 8084),
    feeds into webrtcbin, and handles WHIP signaling with LiveKit.
    """

    def __init__(
        self,
        ws_port: int = 8084,
        whip_url: str = "http://127.0.0.1:7880/whip/ingress/test",
        stun_url: str = "stun://stun.l.google.com:19302",
    ) -> None:
        self._ws_port = ws_port
        self._whip_url = whip_url
        self._stun_url = stun_url
        self._pipeline: Optional[Gst.Pipeline] = None
        self._webrtc: Optional[GstWebRTC.WebRTCBin] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._whip_resource_url: Optional[str] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._connected = asyncio.Event()
        self._failed = False

    async def start(self) -> None:
        """Build pipeline and start WHIP publishing."""
        self._loop = asyncio.get_event_loop()
        self._session = aiohttp.ClientSession()
        self._running = True

        # Build GStreamer pipeline:
        # websocketclientsrc → h265parse → webrtcbin
        pipeline_str = (
            f"websocketclientsrc uri=ws://127.0.0.1:{self._ws_port} "
            f"! h265parse "
            f"! webrtcbin name=webbrtc "
            f"  stun-server={self._stun_url} "
            f"  bundle-policy=max-bundle "
            f"  ice-lite=false "
            f"  latency-min=0 "
            f"  do-fec=false "
            f"  do-clock-signalling=false"
        )

        log.info("WHIP: building pipeline: %s", pipeline_str[:100])
        self._pipeline = Gst.parse_launch(pipeline_str)
        self._webrtc = self._pipeline.get_by_name("webbrtc")

        if self._webrtc is None:
            raise RuntimeError("webrtcbin not found in pipeline")

        # Connect signals
        self._webrtc.connect("on-negotiation-needed", self._on_negotiation_needed)
        self._webrtc.connect("on-ice-candidate", self._on_ice_candidate)
        self._webrtc.connect("on ICE gathering state changed", self._on_ice_gathering)

        # Start pipeline
        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Failed to set pipeline to PLAYING")

        log.info("WHIP: pipeline started, waiting for negotiation...")

        # Wait for connection or timeout
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=15.0)
            log.info("WHIP: connected to LiveKit")
        except asyncio.TimeoutError:
            self._failed = True
            raise RuntimeError("WHIP connection timed out")

    async def stop(self) -> None:
        """Stop WHIP publishing."""
        self._running = False

        # DELETE WHIP resource
        if self._whip_resource_url and self._session:
            try:
                await self._session.delete(self._whip_resource_url)
                log.info("WHIP: resource deleted")
            except Exception:
                pass

        # Stop pipeline
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)

        if self._session:
            await self._session.close()
            self._session = None

        log.info("WHIP: stopped")

    def _on_negotiation_needed(self, element: GstWebRTC.WebRTCBin) -> None:
        """SDP offer ready — create and send to LiveKit."""
        log.info("WHIP: on-negotiation-needed, creating offer")
        promise = Gst.Promise.new()
        promise.connect("interact", self._on_offer_created, element)
        element.emit("create-offer", promise)

    def _on_offer_created(self, promise: Gst.Promise, element: GstWebRTC.WebRTCBin) -> None:
        """Offer created — extract SDP and POST to WHIP endpoint."""
        reply = promise.get_reply()
        offer = reply.get_value("offer")
        sdp_text = offer.get_text()

        log.info("WHIP: SDP offer:\n%s", sdp_text[:200])

        # Set local description
        local_promise = Gst.Promise.new()
        element.emit("set-local-description", offer, local_promise)
        local_promise.interrupt()

        # POST to WHIP endpoint in event loop
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._post_offer(sdp_text), self._loop
            )

    async def _post_offer(self, sdp_text: str) -> None:
        """POST SDP offer to WHIP endpoint, receive answer."""
        if not self._session:
            return

        headers = {"Content-Type": "application/sdp"}
        try:
            log.info("WHIP: POSTing offer to %s", self._whip_url)
            async with self._session.post(
                self._whip_url, data=sdp_text, headers=headers
            ) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    log.error("WHIP: POST failed: %d %s", resp.status, body)
                    self._failed = True
                    return

                # Save resource URL for DELETE on cleanup
                self._whip_resource_url = str(resp.url)
                log.info("WHIP: resource URL: %s", self._whip_resource_url)

                answer_text = await resp.text()
                log.info("WHIP: got SDP answer:\n%s", answer_text[:200])

                # Set answer on webrtcbin
                self._set_answer(answer_text)

        except Exception as e:
            log.error("WHIP: POST error: %s", e)
            self._failed = True

    def _set_answer(self, sdp_text: str) -> None:
        """Parse SDP answer and set it on webrtcbin."""
        sdp = GstSdp.SDPMessage.new()
        GstSdp.sdp_message_parse_buffer(sdp_text.encode(), sdp)

        answer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.ANSWER, sdp
        )
        promise = Gst.Promise.new()
        self._webrtc.emit("set-remote-description", answer, promise)
        promise.interrupt()

        log.info("WHIP: SDP answer set on webrtcbin")

    def _on_ice_candidate(self, element: GstWebRTC.WebRTCBin, mlineindex: int, candidate: str) -> None:
        """ICE candidate generated — send via trickle to WHIP."""
        log.debug("WHIP: ICE candidate: mline=%d %s", mlineindex, candidate)
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._trickle_ice(mlineindex, candidate), self._loop
            )

    async def _trickle_ice(self, mlineindex: int, candidate: str) -> None:
        """Send ICE candidate via WHIP trickle (PATCH)."""
        if not self._session or not self._whip_resource_url:
            log.debug("WHIP: trickle skipped (no session or resource URL)")
            return

        # Format: a=candidate:<candidate> <mline> UDP <priority> <ip> <port> typ host
        sdp_frag = f"a=candidate:{candidate} {mlineindex} UDP 2113937151 0.0.0.0 0 typ host"
        headers = {"Content-Type": "application/trickle-ice-sdpfrag"}
        try:
            async with self._session.patch(
                self._whip_resource_url, data=sdp_frag, headers=headers
            ) as resp:
                log.debug("WHIP: trickle response: %d", resp.status)
        except Exception as e:
            log.debug("WHIP: trickle error: %s", e)

    def _on_ice_gathering(self, element: GstWebRTC.WebRTCBin) -> None:
        """ICE gathering state changed."""
        state = element.get_property("ice-gathering-state")
        log.info("WHIP: ICE gathering state: %s", state)

        if state == GstWebRTC.WebRTCICEGatheringState.COMPLETE:
            log.info("WHIP: ICE gathering complete")
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    self._connected.set(), self._loop
                )
        elif state == GstWebRTC.WebRTCICEGatheringState.FAILED:
            log.error("WHIP: ICE gathering failed")
            self._failed = True
            if self._loop:
                asyncio.run_coroutine_threadsafe(
                    self._connected.set(), self._loop
                )
