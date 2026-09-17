from __future__ import annotations

"""Bounded latest-frame queue for non-blocking ANPR inference (Phase 6B)."""

import threading
from collections import deque
from dataclasses import dataclass, field

from pcn_edge.frames import Frame


@dataclass
class BoundedFrameQueue:
    """Drop oldest frames when full — prefer the latest frame."""

    maxsize: int = 8
    _buf: deque[Frame] = field(default_factory=deque)
    _cond: threading.Condition = field(default_factory=threading.Condition)
    _closed: bool = False
    dropped: int = 0  # dropped on offer (capacity)
    stale_dropped: int = 0  # dropped when taking latest

    def __post_init__(self) -> None:
        self._buf = deque()
        self._cond = threading.Condition()

    def offer(self, frame: Frame) -> bool:
        """Enqueue frame; drop oldest if at capacity. Returns False if closed."""
        with self._cond:
            if self._closed:
                return False
            while len(self._buf) >= self.maxsize:
                self._buf.popleft()
                self.dropped += 1
            self._buf.append(frame)
            self._cond.notify()
            return True

    def get(self, timeout: float | None = 1.0) -> Frame | None:
        """FIFO pop (oldest). Prefer get_latest() for live ANPR."""
        with self._cond:
            if not self._buf and not self._closed:
                self._cond.wait(timeout=timeout)
            if not self._buf:
                return None
            return self._buf.popleft()

    def get_latest(self, timeout: float | None = 1.0) -> Frame | None:
        """Return newest frame; discard older queued frames as stale."""
        with self._cond:
            if not self._buf and not self._closed:
                self._cond.wait(timeout=timeout)
            if not self._buf:
                return None
            while len(self._buf) > 1:
                self._buf.popleft()
                self.stale_dropped += 1
            return self._buf.popleft()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def qsize(self) -> int:
        with self._cond:
            return len(self._buf)
