"""WHIP sender — publishes video to LiveKit via webrtcbin.

Reads RTP from UDP (from GStreamer pipeline), depayloads, and publishes
via webrtcbin to LiveKit WHIP endpoint.

Architecture:
    GStreamer pipeline (separate process):
        appsrc → encode → rtph265pay → udpsink:5004

    This sender:
        udpsrc:5004 → rtph265depay → webrtcbin → LiveKit WHIP

    No re-encoding — H.265 NAL units pass through webrtcbin directly.

Usage:
    from camera.whipsender import WHIPDualSender
    sender = WHIPDualSender(
        whip_url="http://...whip/ingress/...",
        h265_port=5004,
        h264_port=5006,
    )
    await sender.start()
    await sender.stop()
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
gi.require_version("GstRtp", "1.0")
from gi.repository import GLib, Gst, GstRtp, GstWebRTC

log = logging.getLogger("cockpit.camera.whipsender")

Gst.init(None)


class WHIPSender:
    """Single-codec WHIP sender via webrtcbin."""

    def __init__(
        self,
        rtp_port: int = 5004,
        whip_url: str = "http://127.0.0.1:7880/whip/ingress/test",
        stun_url: str = "stun://stun.l.google.com:19302",
        codec: str = "H265",
    ) -> None:
        self._rtp_port = rtp_port
        self._whip_url = whip_url
        self._stun_url = stun_url
        self._codec = codec.upper()
        self._pipeline: Optional[Gst.Pipeline] = None
        self._webrtc: Optional[GstWebRTC.WebRTCBin] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None
        self._whip_resource_url: Optional[str] = None
        self._connected = asyncio.Event()
        self._failed = False
        self._gst_thread: Optional[threading.Thread] = None
        self._bus_watch_id: int = 0
        self._main_loop: Optional[GLib.MainLoop] = None

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set() and not self._failed

    async def start(self) -> None:
        """Build pipeline and start WHIP publishing."""
        self._loop = asyncio.get_event_loop()
        self._session = aiohttp.ClientSession()
        self._running = True
        self._connected.clear()
        self._failed = False

        # Select depayloader based on codec
        depay = "rtph265depay" if self._codec == "H265" else "rtph264depay"

        # Pipeline: udpsrc → depay → webrtcbin
        pipeline_str = (
            f"udpsrc port={self._rtp_port} "
            f"! application/x-rtp,media=video,encoding-name={'H265' if self._codec == 'H265' else 'H264'},payload=96,clock-rate=90000 "
            f"! {depay} "
            f"! webrtcbin name=webbrtc "
            f"  stun-server={self._stun_url} "
            f"  bundle-policy=max-bundle "
            f"  ice-lite=false "
            f"  latency-min=0"
        )

        log.info("WHIP[%s]: building pipeline (port=%d)", self._codec, self._rtp_port)
        self._pipeline = Gst.parse_launch(pipeline_str)
        self._webrtc = self._pipeline.get_by_name("webbrtc")

        if self._webrtc is None:
            raise RuntimeError("webrtcbin not found in pipeline")

        self._webrtc.connect("on-negotiation-needed", self._on_negotiation_needed)
        self._webrtc.connect("on-ice-candidate", self._on_ice_candidate)
        self._webrtc.connect("on ICE gathering state changed", self._on_ice_gathering)

        bus = self._pipeline.get_bus()
        self._bus_watch_id = bus.add_watch(GLib.PRIORITY_NORMAL, self._on_bus_message)

        self._gst_thread = threading.Thread(target=self._run_gst_loop, daemon=True)
        self._gst_thread.start()

        try:
            await asyncio.wait_for(self._connected.wait(), timeout=15.0)
            log.info("WHIP[%s]: connected", self._codec)
        except asyncio.TimeoutError:
            self._failed = True
            raise RuntimeError(f"WHIP[{self._codec}] connection timed out")

    async def stop(self) -> None:
        """Stop WHIP publishing."""
        self._running = False

        # Остановить GLib-цикл
        if self._main_loop and self._main_loop.is_running():
            self._main_loop.quit()
            log.info("WHIP[%s]: GLib main loop quit requested", self._codec)

        # DELETE WHIP resource
        if self._whip_resource_url and self._session:
            try:
                await self._session.delete(self._whip_resource_url)
                log.info("WHIP[%s]: resource deleted", self._codec)
            except Exception as e:
                log.debug("WHIP[%s]: DELETE error: %s", self._codec, e)

        # Stop pipeline
        if self._pipeline:
            self._pipeline.set_state(Gst.State.NULL)
            if self._bus_watch_id:
                GLib.source_remove(self._bus_watch_id)
                self._bus_watch_id = 0

        if self._session:
            await self._session.close()
            self._session = None

        # Дожидаемся завершения потока
        if self._gst_thread and self._gst_thread.is_alive():
            self._gst_thread.join(timeout=1.0)
            if self._gst_thread.is_alive():
                log.warning("WHIP[%s]: GStreamer thread did not finish", self._codec)

        log.info("WHIP[%s]: stopped", self._codec)

    def _run_gst_loop(self) -> None:
        """Run GLib main loop in separate thread."""
        ctx = GLib.MainContext.default()
        loop = GLib.MainLoop.new(ctx, False)
        self._main_loop = loop
        self._pipeline.set_state(Gst.State.PLAYING)
        log.info("WHIP[%s]: pipeline PLAYING", self._codec)
        loop.run()
        log.info("WHIP[%s]: GLib main loop finished", self._codec)

    def _on_bus_message(self, bus, message) -> bool:
        """Handle GStreamer bus messages."""
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            log.error("WHIP[%s]: error: %s %s", self._codec, err.message, debug or "")
            self._failed = True
            if self._loop:
                asyncio.run_coroutine_threadsafe(self._connected.set(), self._loop)
        elif t == Gst.MessageType.WARNING:
            err, debug = message.parse_warning()
            log.warning("WHIP[%s]: warning: %s", self._codec, err.message)
        elif t == Gst.MessageType.EOS:
            log.info("WHIP[%s]: EOS received", self._codec)
            if self._main_loop and self._main_loop.is_running():
                GLib.idle_add(self._main_loop.quit)
        return True

    def _on_negotiation_needed(self, element: GstWebRTC.WebRTCBin) -> None:
        """Create SDP offer when negotiation is needed."""
        log.info("WHIP[%s]: on-negotiation-needed", self._codec)
        promise = Gst.Promise.new()
        promise.connect("interact", self._on_offer_created, element)
        element.emit("create-offer", promise)

    def _on_offer_created(self, promise: Gst.Promise, element: GstWebRTC.WebRTCBin) -> None:
        """Offer created — extract SDP and POST to WHIP endpoint."""
        reply = promise.get_reply()
        if not reply:
            log.error("WHIP[%s]: No reply from create-offer", self._codec)
            return

        offer = reply.get_value("offer")
        sdp_text = offer.get_text()

        log.info("WHIP[%s]: SDP offer:\n%s", self._codec, sdp_text[:300])

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
            log.info("WHIP[%s]: POSTing offer to %s", self._codec, self._whip_url)
            async with self._session.post(
                self._whip_url, data=sdp_text, headers=headers
            ) as resp:
                if resp.status not in (200, 201):
                    body = await resp.text()
                    log.error("WHIP[%s]: POST failed: %d %s", self._codec, resp.status, body)
                    self._failed = True
                    if self._loop:
                        asyncio.run_coroutine_threadsafe(self._connected.set(), self._loop)
                    return

                self._whip_resource_url = str(resp.url)
                answer_text = await resp.text()
                log.info("WHIP[%s]: got SDP answer:\n%s", self._codec, answer_text[:300])

                # Set answer on webrtcbin (must be done in GLib thread)
                GLib.idle_add(self._set_answer, answer_text)

        except Exception as e:
            log.error("WHIP[%s]: POST error: %s", self._codec, e)
            self._failed = True
            if self._loop:
                asyncio.run_coroutine_threadsafe(self._connected.set(), self._loop)

    def _set_answer(self, sdp_text: str) -> bool:
        """Parse SDP answer and set it on webrtcbin."""
        from gi.repository import GstSdp

        sdp = GstSdp.SDPMessage.new()
        GstSdp.sdp_message_parse_buffer(sdp_text.encode(), sdp)

        answer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.ANSWER, sdp
        )
        promise = Gst.Promise.new()
        self._webrtc.emit("set-remote-description", answer, promise)
        promise.interrupt()

        log.info("WHIP[%s]: SDP answer set", self._codec)
        return False

    def _on_ice_candidate(self, element: GstWebRTC.WebRTCBin, mlineindex: int, candidate: str) -> None:
        """ICE candidate generated — send via trickle to WHIP."""
        log.debug("WHIP[%s]: ICE candidate: mline=%d %s", self._codec, mlineindex, candidate[:50])
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._trickle_ice(mlineindex, candidate), self._loop
            )

    async def _trickle_ice(self, mlineindex: int, candidate: str) -> None:
        """Send ICE candidate via WHIP trickle (PATCH)."""
        if not self._session or not self._whip_resource_url:
            log.debug("WHIP[%s]: trickle skipped (no session or resource URL)", self._codec)
            return

        sdp_frag = f"a=candidate:{candidate} {mlineindex} UDP 2113937151 0.0.0.0 0 typ host"
        headers = {"Content-Type": "application/trickle-ice-sdpfrag"}
        try:
            async with self._session.patch(
                self._whip_resource_url, data=sdp_frag, headers=headers
            ) as resp:
                if resp.status < 200 or resp.status >= 300:
                    log.warning("WHIP[%s]: trickle failed: %d", self._codec, resp.status)
                else:
                    log.debug("WHIP[%s]: trickle success: %d", self._codec, resp.status)
        except Exception as e:
            log.debug("WHIP[%s]: trickle error: %s", self._codec, e)

    def _on_ice_gathering(self, element: GstWebRTC.WebRTCBin) -> None:
        """ICE gathering state changed."""
        state = element.get_property("ice-gathering-state")
        log.info("WHIP[%s]: ICE gathering: %s", self._codec, state)

        if state == GstWebRTC.WebRTCICEGatheringState.COMPLETE:
            log.info("WHIP[%s]: ICE gathering complete", self._codec)
            if self._loop:
                asyncio.run_coroutine_threadsafe(self._connected.set(), self._loop)
        elif state == GstWebRTC.WebRTCICEGatheringState.FAILED:
            log.error("WHIP[%s]: ICE gathering failed", self._codec)
            self._failed = True
            if self._loop:
                asyncio.run_coroutine_threadsafe(self._connected.set(), self._loop)


class WHIPDualSender:
    """Dual-codec sender — publishes both H.265 and H.264 to LiveKit.

    LiveKit automatically selects the right codec for each subscriber:
    - H.265: Safari Mac, Chrome (experimental), Edge (experimental)
    - H.264: Firefox, Safari iOS, Chrome/Edge fallback
    """

    def __init__(
        self,
        whip_url: str = "http://127.0.0.1:7880/whip/ingress/test",
        h265_port: int = 5004,
        h264_port: int = 5006,
        stun_url: str = "stun://stun.l.google.com:19302",
    ) -> None:
        self._h265 = WHIPSender(
            rtp_port=h265_port,
            whip_url=whip_url,
            stun_url=stun_url,
            codec="H265",
        )
        self._h264 = WHIPSender(
            rtp_port=h264_port,
            whip_url=whip_url,
            stun_url=stun_url,
            codec="H264",
        )
        self._started = False

    @property
    def is_connected(self) -> bool:
        return self._h265.is_connected or self._h264.is_connected

    async def start(self) -> None:
        """Start both H.265 and H.264 senders."""
        if self._started:
            log.warning("WHIPDual: already started")
            return

        log.info("WHIPDual: starting both codecs")
        errors = []

        # Start H.265
        try:
            await self._h265.start()
            log.info("WHIPDual: H.265 started")
        except Exception as e:
            log.warning("WHIPDual: H.265 failed: %s", e)
            errors.append(f"H265: {e}")

        # Start H.264
        try:
            await self._h264.start()
            log.info("WHIPDual: H.264 started")
        except Exception as e:
            log.warning("WHIPDual: H.264 failed: %s", e)
            errors.append(f"H264: {e}")

        if errors and not self.is_connected:
            raise RuntimeError(f"Both codecs failed: {'; '.join(errors)}")

        self._started = True
        log.info("WHIPDual: H.265=%s, H.264=%s",
                 "OK" if self._h265.is_connected else "FAIL",
                 "OK" if self._h264.is_connected else "FAIL")

    async def stop(self) -> None:
        """Stop both senders."""
        if not self._started:
            return

        log.info("WHIPDual: stopping both codecs")
        await asyncio.gather(
            self._h265.stop(),
            self._h264.stop(),
            return_exceptions=True,
        )
        self._started = False
        log.info("WHIPDual: stopped")
