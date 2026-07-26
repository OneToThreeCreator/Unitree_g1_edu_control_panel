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
        self._whip_sender: Optional[object] = None  # WHIPDualSender instance

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
        """Stop camera server + GStreamer pipeline and WHIP."""
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

        # Stop WHIP publisher
        if self._whip_sender:
            try:
                await self._whip_sender.stop()
            except Exception as e:
                log.warning("Error stopping WHIP sender: %s", e)
            self._whip_sender = None

        self._stop_gstreamer()
        self._state = CameraState.DISABLED
        log.info("Camera stopped → DISABLED")

    async def pause(self) -> None:
        """Stop proxy without killing GStreamer — pipeline stalls via backpressure."""
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

        # Stop WHIP publisher but keep GStreamer running
        if self._whip_sender:
            try:
                await self._whip_sender.stop()
            except Exception as e:
                log.warning("Error stopping WHIP sender: %s", e)
            self._whip_sender = None

        self._state = CameraState.DISABLED
        log.info("Camera paused → DISABLED (GStreamer alive)")

    async def shutdown(self) -> None:
        await self.stop()
        self._state = CameraState.STOPPED

    async def snapshot_jpeg(self) -> Optional[bytes]:
        # TODO: Get from GStreamer appsink
        return None

    def _start_gstreamer(self) -> None:
        """Launch unified GStreamer pipeline:
           - One appsrc → tee → [H.265 RTP→udpsink, H.264 RTP→udpsink, MJPEG→websocketsink]
        """
        if self._gst_process and self._gst_process.poll() is None:
            log.info("GStreamer already running (pid=%s)", self._gst_process.pid)
            return

        encoder_h265 = self._config.gst_encoder_h265
        bitrate_h265 = self._config.gst_bitrate_h265
        encoder_h264 = self._config.gst_encoder_h264
        bitrate_h264 = self._config.gst_bitrate_h264
        w, h, fps = self._config.color_width, self._config.color_height, self._config.color_fps

        import os
        gst_env = os.environ.copy()
        ws_bin = os.path.join(os.path.dirname(os.path.dirname(__file__)), "gstwebsocketsink-bin")
        gst_env["GST_PLUGIN_PATH"] = ws_bin

        # Единый pipeline с tee
        pipeline = (
            f"appsrc name=src is-live=true format=time "
            f"! video/x-raw,format=BGR,width={w},height={h},framerate={fps}/1 "
            f"! tee name=t "
            f"t. ! queue ! videoconvert ! video/x-raw,format=BGR ! jpegenc quality=80 "
            f"  ! websocketsink host=0.0.0.0 port={self._config.ws_raw_bgr_port} "
            f"t. ! queue ! videoconvert ! video/x-raw,format=BGRx ! nvvidconv "
            f"  ! {encoder_h265} bitrate={bitrate_h265} ! h265parse ! rtph265pay config-interval=1 "
            f"  ! udpsink host=127.0.0.1 port={self._config.rtp_h265_port} "
            f"t. ! queue ! videoconvert ! video/x-raw,format=BGRx ! nvvidconv "
            f"  ! {encoder_h264} bitrate={bitrate_h264} ! h264parse ! rtph264pay config-interval=1 "
            f"  ! udpsink host=127.0.0.1 port={self._config.rtp_h264_port}"
        )

        cmd = ["gst-launch-1.0", "-e"] + pipeline.split(" ")
        log.info("Starting unified GStreamer pipeline: %s...", " ".join(cmd[:6]) + "...")

        try:
            self._gst_process = subprocess.Popen(
                cmd, env=gst_env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
            )
            self._log_gst_stderr(self._gst_process)
            log.info("GStreamer unified started (pid=%s)", self._gst_process.pid)
        except FileNotFoundError:
            log.error("gst-launch-1.0 not found")
            return
        except Exception as e:
            log.error("Failed to start GStreamer: %s", e)
            return

        # Запускаем WHIP‑публикацию
        try:
            from .whipsender import WHIPDualSender
            self._whip_sender = WHIPDualSender(
                whip_url=self._config.livekit_whip_url,
                h265_port=self._config.rtp_h265_port,
                h264_port=self._config.rtp_h264_port,
                stun_url=self._config.webrtc_stun_gst,
            )
            asyncio.create_task(self._whip_sender.start())
            log.info("WHIP DualSender started")
        except Exception as e:
            log.error("Failed to start WHIP DualSender: %s", e)

    def _start_gstreamer_dry(self) -> None:
        """Launch GStreamer pipeline with videotestsrc for DRY_RUN mode.

        Unified pipeline with one source and tee for all outputs.
        """
        if self._gst_process and self._gst_process.poll() is None:
            log.info("GStreamer already running (pid=%s)", self._gst_process.pid)
            return

        w, h, fps = self._config.color_width, self._config.color_height, self._config.color_fps

        import os
        gst_env = os.environ.copy()
        ws_bin = os.path.join(os.path.dirname(os.path.dirname(__file__)), "gstwebsocketsink-bin")
        gst_env["GST_PLUGIN_PATH"] = ws_bin

        # Единый pipeline с tee для dry_run
        pipeline = (
            f"videotestsrc is-live=true ! "
            f"videoconvert ! video/x-raw,format=BGR,width={w},height={h},framerate={fps}/1 ! "
            f"tee name=t "
            f"t. ! queue ! jpegenc quality=80 "
            f"  ! websocketsink host=0.0.0.0 port={self._config.ws_raw_bgr_port} "
            f"t. ! queue ! videoconvert ! video/x-raw,format=BGRx ! nvvidconv "
            f"  ! {self._config.gst_encoder_h265} bitrate={self._config.gst_bitrate_h265} "
            f"  ! h265parse ! rtph265pay config-interval=1 "
            f"  ! udpsink host=127.0.0.1 port={self._config.rtp_h265_port} "
            f"t. ! queue ! videoconvert ! video/x-raw,format=BGRx ! nvvidconv "
            f"  ! {self._config.gst_encoder_h264} bitrate={self._config.gst_bitrate_h264} "
            f"  ! h264parse ! rtph264pay config-interval=1 "
            f"  ! udpsink host=127.0.0.1 port={self._config.rtp_h264_port}"
        )

        cmd = ["gst-launch-1.0", "-e"] + pipeline.split(" ")
        log.info("Starting DRY_RUN unified pipeline: %s...", " ".join(cmd[:6]) + "...")

        try:
            self._gst_process = subprocess.Popen(
                cmd, env=gst_env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
            )
            self._log_gst_stderr(self._gst_process)
            log.info("GStreamer DRY_RUN unified started (pid=%s)", self._gst_process.pid)
        except FileNotFoundError:
            log.error("gst-launch-1.0 not found")
        except Exception as e:
            log.error("Failed to start GStreamer DRY_RUN: %s", e)
            return

        # Запускаем WHIP‑публикацию и для dry_run
        try:
            from .whipsender import WHIPDualSender
            self._whip_sender = WHIPDualSender(
                whip_url=self._config.livekit_whip_url,
                h265_port=self._config.rtp_h265_port,
                h264_port=self._config.rtp_h264_port,
                stun_url=self._config.webrtc_stun_gst,
            )
            asyncio.create_task(self._whip_sender.start())
            log.info("WHIP DualSender started (dry_run)")
        except Exception as e:
            log.error("Failed to start WHIP DualSender: %s", e)

    def _log_gst_stderr(self, proc: subprocess.Popen) -> None:
        """Log GStreamer stderr in background thread."""
        def _reader():
            for line in proc.stderr:
                log.warning("GStreamer: %s", line.decode(errors="replace").strip())
        threading.Thread(target=_reader, daemon=True).start()

    def _stop_gstreamer(self) -> None:
        """Stop GStreamer pipeline."""
        if self._gst_process is None:
            return

        if self._gst_process.poll() is not None:
            self._gst_process = None
            return

        try:
            pid = self._gst_process.pid
            self._gst_process.send_signal(signal.SIGINT)
            self._gst_process.wait(timeout=5)
            log.info("GStreamer stopped (pid=%s)", pid)
        except subprocess.TimeoutExpired:
            self._gst_process.kill()
            log.warning("GStreamer killed (pid=%s)", self._gst_process.pid)
        except Exception as e:
            log.warning("Error stopping GStreamer: %s", e)
        finally:
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
            await asyncio.sleep(self._config.teleop_poll_interval_s)

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
        encoder_h264 = self._config.gst_encoder_h264
        bitrate_h264 = self._config.gst_bitrate_h264

        # Pipeline: receive video from Teleop → tee → MJPEG / raw BGR
        pipeline = (
            f"websocketclientsrc uri={ws_url}?codec={codec} "
            f"! h265parse ! tee name=t "
            f"t. ! queue ! jpegenc "
            f"! websocketsink host=0.0.0.0 port={self._config.ws_raw_bgr_port + 2} "
            f"t. ! queue ! videoconvert "
            f"! video/x-raw,format=BGR "
            f"! websocketsink host=0.0.0.0 port={self._config.ws_raw_bgr_port} "
            f"t. ! queue ! h265parse ! decodebin ! videoconvert ! video/x-raw,format=BGRx "
            f"! nvvidconv ! {encoder_h264} bitrate={bitrate_h264} ! h264parse ! tee name=enc264 "
            f"enc264. ! queue ! websocketsink host=0.0.0.0 port={self._config.ws_raw_bgr_port + 4}"
        )

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
