"""Local camera backend — RealSense capture + PyAV H.264 encoding."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import AsyncIterator, Optional

from .base import BackendType, Frame, VideoBackend
from ..config import CameraConfig

log = logging.getLogger("cockpit.camera.local")


class LocalBackend(VideoBackend):
    """Capture from RealSense via pyrealsense2, encode via PyAV."""

    def __init__(self, config: CameraConfig) -> None:
        self._config = config
        self._pipeline = None  # rs.pipeline
        self._align = None
        self._running = False
        self._capture_thread: Optional[threading.Thread] = None
        self._frame_queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=2)
        self._raw_queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=2)
        self._latest_jpeg: Optional[bytes] = None
        self._lock = threading.Lock()
        self._encoder = None  # PyAV CodecContext
        self._depth_scale = 0.001

    @property
    def backend_type(self) -> BackendType:
        return BackendType.LOCAL

    @property
    def is_active(self) -> bool:
        return self._running

    async def start(self) -> None:
        import pyrealsense2 as rs
        import numpy as np
        import av

        self._pipeline = rs.pipeline()
        rs_config = rs.config()

        # Build fallback chain: full → no-depth → lower-res
        attempts = []
        if self._config.depth_enabled:
            attempts.append((
                f"{self._config.color_width}x{self._config.color_height}@{self._config.color_fps} "
                f"+ depth {self._config.depth_width}x{self._config.depth_height}@{self._config.depth_fps}",
                self._config.color_width, self._config.color_height, self._config.color_fps,
                self._config.depth_width, self._config.depth_height, self._config.depth_fps,
            ))
        attempts.append((
            f"{self._config.color_width}x{self._config.color_height}@{self._config.color_fps} (no depth)",
            self._config.color_width, self._config.color_height, self._config.color_fps,
            0, 0, 0,
        ))
        attempts.append((
            f"640x480@15 (no depth)",
            640, 480, 15,
            0, 0, 0,
        ))

        profile = None
        used_depth = False
        for desc, cw, ch, cfps, dw, dh, dfps in attempts:
            try:
                test_config = rs.config()
                test_config.enable_stream(rs.stream.color, cw, ch, rs.format.bgr8, cfps)
                if dw and dh and dfps:
                    test_config.enable_stream(rs.stream.depth, dw, dh, rs.format.z16, dfps)
                    used_depth = True
                else:
                    used_depth = False

                # Tear down previous attempt if any
                if self._pipeline:
                    try:
                        self._pipeline.stop()
                    except Exception:
                        pass
                    self._pipeline = rs.pipeline()

                profile = self._pipeline.start(test_config)

                # Update actual resolution (may differ from requested)
                self._config.color_width = cw
                self._config.color_height = ch
                self._config.color_fps = cfps
                if used_depth:
                    self._config.depth_width = dw
                    self._config.depth_height = dh
                    self._config.depth_fps = dfps
                    self._align = rs.align(rs.stream.color)
                else:
                    self._align = None

                log.info("RealSense: resolved stream config: %s", desc)
                break
            except Exception as e:
                log.warning("RealSense: config failed (%s): %s", desc, e)
                profile = None
                continue

        if profile is None:
            raise RuntimeError("Couldn't resolve any RealSense stream configuration")

        # Get depth scale
        device = profile.get_device()
        depth_sensor = device.first_depth_sensor()
        self._depth_scale = float(depth_sensor.get_depth_scale())

        # Disable IR emitter if configured
        if self._config.disable_ir_emitter:
            for opt_name in ("emitter_enabled", "laser_power"):
                option = getattr(rs.option, opt_name, None)
                if option is not None and depth_sensor.supports(option):
                    try:
                        depth_sensor.set_option(option, 0)
                    except Exception:
                        pass

        # Init PyAV encoder (mode='w' for encoding)
        codec_name = self._config.ffmpeg_encoder
        self._init_encoder(codec_name, self._config.color_width,
                           self._config.color_height, self._config.color_fps)

        self._running = True
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread.start()
        log.info(
            "LocalBackend started: %dx%d@%d, encoder=%s, bitrate=%dkbps, depth=%s",
            self._config.color_width, self._config.color_height, self._config.color_fps,
            codec_name, self._config.ffmpeg_bitrate, self._align is not None,
        )

    async def stop(self) -> None:
        self._running = False
        if self._capture_thread:
            self._capture_thread.join(timeout=2.0)
        if self._encoder:
            try:
                self._encoder.close()
            except Exception:
                pass
            self._encoder = None
        if self._pipeline:
            try:
                self._pipeline.stop()
            except Exception:
                pass
            self._pipeline = None
        self._align = None
        log.info("LocalBackend stopped")

    async def frames(self) -> AsyncIterator[Frame]:
        while self._running:
            try:
                frame = await asyncio.wait_for(self._frame_queue.get(), timeout=1.0)
                yield frame
            except asyncio.TimeoutError:
                continue

    async def raw_frames(self) -> AsyncIterator[Frame]:
        """Raw BGR frames before encoding (for YOLO, etc.)."""
        while self._running:
            try:
                frame = await asyncio.wait_for(self._raw_queue.get(), timeout=1.0)
                yield frame
            except asyncio.TimeoutError:
                continue

    async def snapshot_jpeg(self) -> Optional[bytes]:
        with self._lock:
            return self._latest_jpeg

    def _init_encoder(self, codec_name: str, width: int, height: int, fps: int) -> None:
        """Create PyAV encoder context."""
        import av
        from fractions import Fraction

        if self._encoder:
            try:
                self._encoder.close()
            except Exception:
                pass
        self._encoder = av.codec.CodecContext.create(codec_name, 'w')
        self._encoder.width = width
        self._encoder.height = height
        self._encoder.time_base = Fraction(1, fps)
        self._encoder.pix_fmt = "yuv420p"
        self._encoder.options = {
            "b": f"{self._config.ffmpeg_bitrate}k",
            "preset": self._config.ffmpeg_preset,
        }

    def _capture_loop(self) -> None:
        """Thread: grab frames from RealSense, encode via PyAV."""
        import numpy as np
        import av
        import cv2

        encode_errors = 0

        while self._running:
            try:
                frames = self._pipeline.wait_for_frames()
                if self._align:
                    frames = self._align.process(frames)

                color_frame = frames.get_color_frame()
                if not color_frame:
                    continue

                color = np.asanyarray(color_frame.get_data())

                # Depth (if enabled)
                depth_data = None
                depth_frame = frames.get_depth_frame() if self._config.depth_enabled else None
                if depth_frame:
                    depth_arr = np.asanyarray(depth_frame.get_data())
                    depth_data = depth_arr.tobytes()

                # Push raw BGR frame
                raw_frame = Frame(
                    data=color.tobytes(),
                    pts_ms=time.monotonic() * 1000,
                    width=self._config.color_width,
                    height=self._config.color_height,
                    format="bgr",
                    depth=depth_data,
                    depth_width=self._config.depth_width if depth_data else 0,
                    depth_height=self._config.depth_height if depth_data else 0,
                )
                if self._raw_queue.full():
                    try:
                        self._raw_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                self._raw_queue.put_nowait(raw_frame)

                # Encode to H.264 via PyAV
                video_frame = av.VideoFrame.from_ndarray(color, format="bgr24")
                for packet in self._encoder.encode(video_frame):
                    nal_bytes = bytes(packet)
                    encoded_frame = Frame(
                        data=nal_bytes,
                        pts_ms=time.monotonic() * 1000,
                        width=self._config.color_width,
                        height=self._config.color_height,
                        format=self._get_codec_format(),
                    )
                    if self._frame_queue.full():
                        try:
                            self._frame_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    self._frame_queue.put_nowait(encoded_frame)

                encode_errors = 0  # reset on success

                # JPEG snapshot (encode every 10th frame)
                if int(time.monotonic() * 10) % 10 == 0:
                    ok, jpeg = cv2.imencode(".jpg", color, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if ok:
                        with self._lock:
                            self._latest_jpeg = jpeg.tobytes()

            except Exception as e:
                encode_errors += 1
                log.warning("LocalBackend capture error (%d): %s", encode_errors, e)
                if encode_errors == 3:
                    for fallback in ("h264_v4l2m2m", "libx264"):
                        log.warning("Encoder failing — trying %s", fallback)
                        try:
                            self._init_encoder(fallback, self._config.color_width,
                                                self._config.color_height, self._config.color_fps)
                            encode_errors = 0
                            break
                        except Exception as e2:
                            log.warning("%s init failed: %s", fallback, e2)
                time.sleep(0.1)

    def _get_codec_format(self) -> str:
        encoder = self._config.ffmpeg_encoder
        if "h265" in encoder or "hevc" in encoder:
            return "h265"
        if "av1" in encoder:
            return "av1"
        return "h264"
