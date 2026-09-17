from __future__ import annotations

"""Optional rate-limited debug frame dumps (does not affect production ANPR path)."""

import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from pcn_edge.frames import Frame

logger = logging.getLogger(__name__)


class DebugSampledFrameSaver:
    """Save sampled frames at a capped FPS with rotation. Disabled when inactive."""

    def __init__(
        self,
        dest_dir: str,
        *,
        fps: float = 1.0,
        max_files: int = 100,
        enabled: bool = False,
    ) -> None:
        self.dest_dir = Path(dest_dir)
        self.fps = max(0.01, float(fps))
        self.min_interval = 1.0 / self.fps
        self.max_files = max(1, int(max_files))
        self.enabled = enabled
        self._last_save_mono = 0.0
        self._lock = threading.Lock()
        self.saved_count = 0

    def maybe_save(self, frame: Frame) -> Path | None:
        if not self.enabled:
            return None
        now = time.monotonic()
        with self._lock:
            if self._last_save_mono and (now - self._last_save_mono) < self.min_interval:
                return None
            self._last_save_mono = now

        # Always copy bytes so we persist the frame payload as received (never a shared buffer).
        payload = bytes(frame.data)
        from pcn_edge.frame_diag import content_hash_jpeg

        chash = frame.content_hash or content_hash_jpeg(payload)

        self.dest_dir.mkdir(parents=True, exist_ok=True)
        ts = frame.captured_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        stamp = ts.astimezone(UTC).strftime("%Y%m%d_%H%M%S_%f")[:-3]
        path = self.dest_dir / f"{frame.camera_id}_{stamp}_seq{frame.sequence}_{chash}.jpg"
        path.write_bytes(payload)
        self.saved_count += 1
        self._rotate()
        logger.info(
            "debug.frame.saved path=%s seq=%s hash=%s size=%s",
            path,
            frame.sequence,
            chash,
            len(payload),
        )
        return path

    def _rotate(self) -> None:
        files = sorted(self.dest_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
        excess = len(files) - self.max_files
        for old in files[: max(0, excess)]:
            try:
                old.unlink()
            except OSError:
                pass
