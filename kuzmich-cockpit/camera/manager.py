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
        self._gst_depth_process: Optional[subprocess.Popen] = None

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
        """Launch GStreamer pipeline (color + optional depth as separate processes)."""
        if self._gst_process and self._gst_process.poll() is None:
            log.info("GStreamer already running (pid=%s)", self._gst_process.pid)
            return

        encoder = self._config.gst_encoder
        bitrate = self._config.gst_bitrate
        w, h, fps = self._config.color_width, self._config.color_height, self._config.color_fps
        stun = self._config.webrtc_stun_url

        # Color pipeline
        color_pipeline = (
            f"appsrc name=src is-live=true format=time "
            f"! video/x-raw,format=BGR,width={w},height={h},framerate={fps}/1 "
            f"! videoconvert ! nvvideoconvert "
            f"! video/x-raw(memory:NVMM),format=NV12 "
            f"! {encoder} bitrate={bitrate} ! h265parse "
            f"! tee name=t "
            f"t. ! queue ! webrtcbin stun-server={stun} "
            f"t. ! queue ! jpegenc "
            f"! websocketsink host=0.0.0.0 port={self._config.ws_raw_bgr_port + 2} "
            f"t. ! queue ! videoconvert "
            f"! video/x-raw,format=BGR "
            f"! websocketsink host=0.0.0.0 port={self._config.ws_raw_bgr_port}"
        )

        cmd_color = ["gst-launch-1.0", "-e", "-c", color_pipeline]
        log.info("Starting GStreamer color: %s...", " ".join(cmd_color[:6]))

        try:
            self._gst_process = subprocess.Popen(
                cmd_color, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
            )
            self._log_gst_stderr(self._gst_process)
            log.info("GStreamer color started (pid=%s)", self._gst_process.pid)
        except FileNotFoundError:
            log.error("gst-launch-1.0 not found")
            return
        except Exception as e:
            log.error("Failed to start GStreamer: %s", e)
            return

        # Depth pipeline (separate process)
        if self._config.depth_enabled:
            dw, dh, dfps = self._config.depth_width, self._config.depth_height, self._config.depth_fps
            depth_pipeline = (
                f"appsrc name=depth_src is-live=true format=time "
                f"! video/x-raw,format=GRAY16_LE,width={dw},height={dh},framerate={dfps}/1 "
                f"! videoconvert "
                f"! websocketsink host=0.0.0.0 port={self._config.ws_depth_port}"
            )
            cmd_depth = ["gst-launch-1.0", "-e", "-c", depth_pipeline]
            try:
                self._gst_depth_process = subprocess.Popen(
                    cmd_depth, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
                )
                self._log_gst_stderr(self._gst_depth_process)
                log.info("GStreamer depth started (pid=%s)", self._gst_depth_process.pid)
            except Exception as e:
                log.warning("Failed to start depth pipeline: %s", e)

    def _log_gst_stderr(self, proc: subprocess.Popen) -> None:
        """Log GStreamer stderr in background thread."""
        def _reader():
            for line in proc.stderr:
                log.warning("GStreamer: %s", line.decode(errors="replace").strip())
        threading.Thread(target=_reader, daemon=True).start()

    def _stop_gstreamer(self) -> None:
        """Stop all GStreamer pipelines."""
        for proc in [self._gst_process, self._gst_depth_process]:
            if proc is None:
                continue
            if proc.poll() is not None:
                continue
            try:
                pid = proc.pid
                proc.send_signal(signal.SIGINT)
                proc.wait(timeout=5)
                log.info("GStreamer stopped (pid=%s)", pid)
            except subprocess.TimeoutExpired:
                proc.kill()
                log.warning("GStreamer killed (pid=%s)", proc.pid)
            except Exception as e:
                log.warning("Error stopping GStreamer: %s", e)
        self._gst_process = None
        self._gst_depth_process = None

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
            f"t. ! queue ! jpegenc "
            f"! websocketsink host=0.0.0.0 port={self._config.ws_raw_bgr_port + 2} "
            f"t. ! queue ! videoconvert "
            f"! video/x-raw,format=BGR "
            f"! websocketsink host=0.0.0.0 port={self._config.ws_raw_bgr_port}"
        )

        # Use -c flag to pass pipeline as string
        cmd = ["gst-launch-1.0", "-e", "-c", pipeline]
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
