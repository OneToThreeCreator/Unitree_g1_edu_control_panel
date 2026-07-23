"""Camera manager — state machine for camera lifecycle."""
from __future__ import annotations

import asyncio
import logging
import subprocess
import signal
import threading
from enum import Enum
from typing import Any, Dict, Optional

from .config import CameraConfig
from .teleop import TeleopBackend

log = logging.getLogger("cockpit.camera.manager")


class CameraState(str, Enum):
    STOPPED = "stopped"
    DISABLED = "disabled"
    LOCAL = "local"
    RELAY = "relay"
    SWITCHING = "switching"


class CameraManager:
    """Camera lifecycle manager.

    Manages GStreamer pipeline + Teleop relay.
    GStreamer starts/stops with the camera server.
    """

    def __init__(self, config: CameraConfig, teleop_bridge: object = None) -> None:
        self._config = config
        self._teleop = teleop_bridge
        self._state = CameraState.STOPPED
        self._poll_task: Optional[asyncio.Task] = None
        self._gst_process: Optional[subprocess.Popen] = None

    @property
    def state(self) -> CameraState:
        return self._state

    @property
    def active_backend_type(self) -> Optional[str]:
        if self._state == CameraState.LOCAL:
            return "local"
        if self._state == CameraState.RELAY:
            return "teleop"
        return None

    @property
    def config(self) -> CameraConfig:
        return self._config

    def status(self) -> Dict[str, Any]:
        return {
            "state": self._state.value,
            "backend": self.active_backend_type,
            "gst_running": self._gst_process is not None and self._gst_process.poll() is None,
        }

    async def start(self) -> None:
        """Start camera manager + GStreamer pipeline."""
        if self._state not in (CameraState.STOPPED, CameraState.DISABLED):
            return

        # Check if Teleop is already running
        teleop_running = False
        if self._teleop:
            try:
                teleop_running = await self._teleop.is_running()
            except Exception:
                pass

        if teleop_running:
            log.info("Teleop already running → RELAY mode")
            self._state = CameraState.RELAY
        else:
            log.info("Starting LOCAL mode")
            self._state = CameraState.LOCAL

        # Start GStreamer pipeline
        self._start_gstreamer()

        # Start Teleop state polling
        if self._poll_task is None or self._poll_task.done():
            self._poll_task = asyncio.create_task(self._teleop_poll_loop())

    async def stop(self) -> None:
        """Stop camera server + GStreamer pipeline."""
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

        self._stop_gstreamer()
        self._state = CameraState.DISABLED
        log.info("Camera stopped → DISABLED")

    async def shutdown(self) -> None:
        await self.stop()
        self._state = CameraState.STOPPED

    async def snapshot_jpeg(self) -> Optional[bytes]:
        # TODO: Get from GStreamer appsink
        return None

    def _start_gstreamer(self) -> None:
        """Launch GStreamer pipeline."""
        if self._gst_process and self._gst_process.poll() is None:
            log.info("GStreamer already running (pid=%s)", self._gst_process.pid)
            return

        # Build GStreamer pipeline command
        encoder = self._config.gst_encoder
        bitrate = self._config.gst_bitrate
        w, h, fps = self._config.color_width, self._config.color_height, self._config.fps
        stun = self._config.webrtc_stun_url

        pipeline = (
            f"appsrc name=src is-live=true format=time "
            f"video/x-raw,format=BGR,width={w},height={h},framerate={fps}/1 "
            f"! videoconvert ! nvvideoconvert "
            f"video/x-raw(memory:NVMM),format=NV12 "
            f"! {encoder} bitrate={bitrate} ! h265parse "
            f"! tee name=t "
            f"t. ! queue ! webrtcbin stun-server={stun} "
            f"t. ! queue ! jpegenc ! multipartmux boundary=frame "
            f"! websocketserver host=0.0.0.0 port=8084 "
            f"t. ! queue ! videoconvert video/x-raw,format=BGR "
            f"! websocketserver host=0.0.0.0 port=8082"
        )

        # Add depth pipeline if enabled
        if self._config.depth_enabled:
            dw, dh, dfps = self._config.depth_width, self._config.depth_height, self._config.depth_fps
            depth_pipeline = (
                f"appsrc name=depth_src is-live=true format=time "
                f"video/x-raw,format=GRAY16_LE,width={dw},height={dh},framerate={dfps}/1 "
                f"! videoconvert "
                f"! websocketserver host=0.0.0.0 port={self._config.ws_depth_port}"
            )
            pipeline += f" {depth_pipeline}"

        cmd = ["gst-launch-1.0", "-e"] + pipeline.split()
        log.info("Starting GStreamer: %s", " ".join(cmd[:10]) + "...")

        try:
            self._gst_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
            )
            log.info("GStreamer started (pid=%s)", self._gst_process.pid)

            # Log GStreamer stderr in background thread
            def _log_gst_stderr():
                for line in self._gst_process.stderr:
                    log.warning("GStreamer: %s", line.decode(errors="replace").strip())
            threading.Thread(target=_log_gst_stderr, daemon=True).start()

            # Check if process started successfully
            import time
            time.sleep(0.5)
            if self._gst_process.poll() is not None:
                log.error("GStreamer exited immediately with code %s", self._gst_process.returncode)

        except FileNotFoundError:
            log.error("gst-launch-1.0 not found. Is GStreamer installed?")
        except Exception as e:
            log.error("Failed to start GStreamer: %s", e)

    def _stop_gstreamer(self) -> None:
        """Stop GStreamer pipeline."""
        if self._gst_process is None:
            return
        if self._gst_process.poll() is not None:
            self._gst_process = None
            return
        try:
            pid = self._gst_process.pid
            self._gst_process.send_signal(signal.SIGINT)  # gst-launch handles SIGINT for clean shutdown
            self._gst_process.wait(timeout=5)
            log.info("GStreamer stopped (pid=%s)", pid)
        except subprocess.TimeoutExpired:
            self._gst_process.kill()
            log.warning("GStreamer killed (pid=%s)", self._gst_process.pid)
        except Exception as e:
            log.warning("Error stopping GStreamer: %s", e)
        self._gst_process = None

    async def _teleop_poll_loop(self) -> None:
        if not self._teleop:
            return
        while True:
            try:
                teleop_active = await self._teleop.is_running()
                if teleop_active and self._state == CameraState.LOCAL:
                    log.info("Teleop detected → RELAY mode")
                    self._state = CameraState.RELAY
                    self._stop_gstreamer()
                    self._start_gstreamer_relay()
                elif not teleop_active and self._state == CameraState.RELAY:
                    log.info("Teleop stopped → LOCAL mode")
                    self._state = CameraState.LOCAL
                    self._stop_gstreamer()
                    self._start_gstreamer()
            except Exception as e:
                log.debug("Teleop poll error: %s", e)
            await asyncio.sleep(self._config.teleop.poll_interval)

    def _start_gstreamer_relay(self) -> None:
        """Launch GStreamer pipeline for RELAY mode (receives H.265 from Teleop WebSocket)."""
        if self._gst_process and self._gst_process.poll() is None:
            self._stop_gstreamer()

        ws_url = self._config.teleop_ws_url
        codec = self._config.teleop_codec
        stun = self._config.webrtc_stun_url

        # Pipeline: receive H.265 from Teleop WebSocket → tee → WebRTC / MJPEG / raw BGR
        pipeline = (
            f"websocketclientsrc uri={ws_url}?codec={codec} "
            f"! h265parse ! tee name=t "
            f"t. ! queue ! webrtcbin stun-server={stun} "
            f"t. ! queue ! jpegenc ! multipartmux boundary=frame "
            f"! websocketserver host=0.0.0.0 port={self._config.ws_raw_bgr_port + 2} "
            f"t. ! queue ! videoconvert video/x-raw,format=BGR "
            f"! websocketserver host=0.0.0.0 port={self._config.ws_raw_bgr_port}"
        )

        cmd = ["gst-launch-1.0", "-e"] + pipeline.split()
        log.info("Starting GStreamer RELAY: %s", " ".join(cmd[:10]) + "...")

        try:
            self._gst_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
            )
            log.info("GStreamer RELAY started (pid=%s)", self._gst_process.pid)
        except FileNotFoundError:
            log.error("gst-launch-1.0 not found")
        except Exception as e:
            log.error("Failed to start GStreamer RELAY: %s", e)
