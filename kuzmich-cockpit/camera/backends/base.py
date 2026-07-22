"""Abstract video backend interface and shared types."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import AsyncIterator, Optional


class BackendType(str, Enum):
    LOCAL = "local"
    TELEOP = "teleop"


class Frame:
    """Single video frame."""

    __slots__ = ("data", "pts_ms", "width", "height", "format", "depth", "depth_width", "depth_height")

    def __init__(
        self,
        data: bytes,
        pts_ms: float,
        width: int,
        height: int,
        format: str = "h264",
        depth: Optional[bytes] = None,
        depth_width: int = 0,
        depth_height: int = 0,
    ) -> None:
        self.data = data
        self.pts_ms = pts_ms
        self.width = width
        self.height = height
        self.format = format  # "h264", "h265", "av1", "jpeg", "bgr"
        self.depth = depth
        self.depth_width = depth_width
        self.depth_height = depth_height

    @classmethod
    def now(cls, data: bytes, width: int, height: int, fmt: str = "h264") -> Frame:
        return cls(data, time.monotonic() * 1000, width, height, fmt)


class VideoBackend(ABC):
    """Abstract video source — all backends produce Frame objects."""

    @property
    @abstractmethod
    def backend_type(self) -> BackendType: ...

    @property
    @abstractmethod
    def is_active(self) -> bool: ...

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def frames(self) -> AsyncIterator[Frame]: ...

    async def snapshot_jpeg(self) -> Optional[bytes]:
        return None

    async def raw_frames(self) -> AsyncIterator[Frame]:
        """Raw BGR frames before encoding (for YOLO, etc.)."""
        raise NotImplementedError
