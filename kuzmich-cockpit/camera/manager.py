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

        if self._config.dry_run:
            log.info("Starting DRY_RUN mode (videotestsrc)")
            self._state = CameraState.LOCAL
            self._start_gstreamer_dry()
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

    async def pause(self) -> None:
        """Stop proxy without killing GStreamer — pipeline stalls via backpressure."""
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

        self._state = CameraState.DISABLED
        log.info("Camera paused → DISABLED (GStreamer alive)")

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

        encoder_h265 = self._config.gst_encoder_h265
        bitrate_h265 = self._config.gst_bitrate_h265
        w, h, fps = self._config.color_width, self._config.color_height, self._config.color_fps

        # GStreamer env — add websocketsink plugin path
        import os
        gst_env = os.environ.copy()
        ws_bin = os.path.join(os.path.dirname(os.path.dirname(__file__)), "gstwebsocketsink-bin")
        gst_env["GST_PLUGIN_PATH"] = ws_bin

        # Color pipeline (dual-encode: H.265 + H.264 for browser fallback):
        # appsrc (BGR) → tee
        #   ├── queue → videoconvert → BGRx → nvvidconv → H.265 encoder → tee
        #   │   ├── websocketsink:8084 (H.265)
        #   │   └── rtph265pay → udpsink:5004 (RTP H.265 for Janus)
        #   ├── queue → videoconvert → BGRx → nvvidconv → H.264 encoder → tee
        #   │   ├── websocketsink:8086 (H.264)
        #   │   └── rtph264pay → udpsink:5006 (RTP H.264 for Janus)
        #   └── queue → jpegenc → websocketsink:8082 (MJPEG)
        encoder_h264 = self._config.gst_encoder_h264
        bitrate_h264 = self._config.gst_bitrate_h264
        rtp_port = self._config.janus_rtp_h265_port
        rtp_h264_port = self._config.janus_rtp_h264_port
        color_pipeline = (
            f"appsrc name=src is-live=true format=time "
            f"! video/x-raw,format=BGR,width={w},height={h},framerate={fps}/1 "
            f"! tee name=t "
            f"t. ! queue ! videoconvert ! video/x-raw,format=BGRx "
            f"! nvvidconv ! {encoder_h265} bitrate={bitrate_h265} ! h265parse ! tee name=enc "
            f"enc. ! queue ! websocketsink host=0.0.0.0 port={self._config.ws_raw_bgr_port + 2} "
            f"enc. ! queue ! rtph265pay config-interval=1 ! udpsink host=127.0.0.1 port={rtp_port} "
            f"t. ! queue ! videoconvert ! video/x-raw,format=BGRx "
            f"! nvvidconv ! {encoder_h264} bitrate={bitrate_h264} ! h264parse ! tee name=enc264 "
            f"enc264. ! queue ! websocketsink host=0.0.0.0 port={self._config.ws_raw_bgr_port + 4} "
            f"enc264. ! queue ! rtph264pay config-interval=1 ! udpsink host=127.0.0.1 port={rtp_h264_port} "
            f"t. ! queue ! jpegenc quality=80 "
            f"! websocketsink host=0.0.0.0 port={self._config.ws_raw_bgr_port}"
        )

        # Split pipeline into argv (caps filters have no spaces, so split is safe)
        cmd_color = ["gst-launch-1.0", "-e"] + color_pipeline.split(" ")
        log.info("Starting GStreamer color: %s...", " ".join(cmd_color[:8]) + "...")

        try:
            self._gst_process = subprocess.Popen(
                cmd_color, env=gst_env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
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
            cmd_depth = ["gst-launch-1.0", "-e"] + depth_pipeline.split(" ")
            try:
                self._gst_depth_process = subprocess.Popen(
                    cmd_depth, env=gst_env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                    preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
                )
                self._log_gst_stderr(self._gst_depth_process)
                log.info("GStreamer depth started (pid=%s)", self._gst_depth_process.pid)
            except Exception as e:
                log.warning("Failed to start depth pipeline: %s", e)

    def _start_gstreamer_dry(self) -> None:
        """Launch GStreamer pipeline with videotestsrc for DRY_RUN mode."""
        if self._gst_process and self._gst_process.poll() is None:
            log.info("GStreamer already running (pid=%s)", self._gst_process.pid)
            return

        w, h, fps = self._config.color_width, self._config.color_height, self._config.color_fps

        import os
        gst_env = os.environ.copy()
        ws_bin = os.path.join(os.path.dirname(os.path.dirname(__file__)), "gstwebsocketsink-bin")
        gst_env["GST_PLUGIN_PATH"] = ws_bin

        # DRY_RUN pipeline: videotestsrc → tee → MJPEG / raw BGR / RTP H.265+H.264
        # All queues use leaky=downstream to prevent tee blocking on slow branches
        rtp_port = self._config.janus_rtp_h265_port
        rtp_h264_port = self._config.janus_rtp_h264_port
        color_pipeline = (
            f"videotestsrc is-live=true ! "
            f"videoconvert ! video/x-raw,format=BGR,width={w},height={h},framerate={fps}/1 ! "
            f"tee name=t "
            f"t. ! queue leaky=downstream max-size-buffers=1 ! jpegenc quality=80 "
            f"! websocketsink host=0.0.0.0 port={self._config.ws_raw_bgr_port} "
            f"t. ! queue leaky=downstream max-size-buffers=1 ! videoconvert ! video/x-raw,format=BGR "
            f"! websocketsink host=0.0.0.0 port={self._config.ws_raw_bgr_port + 2} "
            f"t. ! queue leaky=downstream max-size-buffers=1 ! videoconvert ! video/x-raw,format=I420 "
            f"! x265enc key-int-max=30 speed-preset=ultrafast ! h265parse ! rtph265pay config-interval=1 "
            f"! udpsink host=127.0.0.1 port={rtp_port} "
            f"t. ! queue leaky=downstream max-size-buffers=1 ! videoconvert ! video/x-raw,format=I420 "
            f"! x264enc tune=zerolatency speed-preset=ultrafast ! h264parse ! rtph264pay config-interval=1 "
            f"! udpsink host=127.0.0.1 port={rtp_h264_port}"
        )

        cmd_color = ["gst-launch-1.0", "-e"] + color_pipeline.split(" ")
        log.info("Starting GStreamer DRY_RUN: %s...", " ".join(cmd_color[:8]) + "...")
        log.info("MJPEG will be on ws://0.0.0.0:%s", self._config.ws_raw_bgr_port)

        try:
            self._gst_process = subprocess.Popen(
                cmd_color, env=gst_env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
            )
            self._log_gst_stderr(self._gst_process)
            log.info("GStreamer DRY_RUN started (pid=%s)", self._gst_process.pid)
        except FileNotFoundError:
            log.error("gst-launch-1.0 not found")
        except Exception as e:
            log.error("Failed to start GStreamer DRY_RUN: %s", e)

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

        import os
        gst_env = os.environ.copy()
        ws_bin = os.path.join(os.path.dirname(os.path.dirname(__file__)), "gstwebsocketsink-bin")
        gst_env["GST_PLUGIN_PATH"] = ws_bin

        ws_url = self._config.teleop_ws_url
        codec = self._config.teleop_codec
        stun = self._config.webrtc_stun_url
        rtp_port = self._config.janus_rtp_h265_port
        rtp_h264_port = self._config.janus_rtp_h264_port
        encoder_h264 = self._config.gst_encoder_h264
        bitrate_h264 = self._config.gst_bitrate_h264

        # Pipeline: receive H.265 from Teleop → tee → MJPEG / raw BGR / RTP H.265+H.264
        pipeline = (
            f"websocketclientsrc uri={ws_url}?codec={codec} "
            f"! h265parse ! tee name=t "
            f"t. ! queue ! jpegenc "
            f"! websocketsink host=0.0.0.0 port={self._config.ws_raw_bgr_port + 2} "
            f"t. ! queue ! videoconvert "
            f"! video/x-raw,format=BGR "
            f"! websocketsink host=0.0.0.0 port={self._config.ws_raw_bgr_port} "
            f"t. ! queue ! rtph265pay config-interval=1 "
            f"! udpsink host=127.0.0.1 port={rtp_port} "
            f"t. ! queue ! h265parse ! decodebin ! videoconvert ! video/x-raw,format=BGRx "
            f"! nvvidconv ! {encoder_h264} bitrate={bitrate_h264} ! h264parse ! rtph264pay config-interval=1 "
            f"! udpsink host=127.0.0.1 port={rtp_h264_port}"
        )

        # Split pipeline into argv
        cmd = ["gst-launch-1.0", "-e"] + pipeline.split(" ")
        log.info("Starting GStreamer RELAY: %s...", " ".join(cmd[:8]) + "...")

        try:
            self._gst_process = subprocess.Popen(
                cmd,
                env=gst_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
            )
            log.info("GStreamer RELAY started (pid=%s)", self._gst_process.pid)
        except FileNotFoundError:
            log.error("gst-launch-1.0 not found")
        except Exception as e:
            log.error("Failed to start GStreamer RELAY: %s", e)
