from __future__ import annotations

"""Lightweight frame-change diagnostics (hash / identity) — no OCR dependency."""

import hashlib
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class FrameDiag:
    sequence: int
    width: int | None
    height: int | None
    content_hash: str
    byte_size: int
    captured_at_iso: str | None = None


def content_hash_jpeg(data: bytes, *, thumb: int = 32) -> str:
    """Fast content fingerprint: SHA1 of a tiny grayscale thumbnail (or raw JPEG).

    Prefer OpenCV thumbnail so visually identical re-encodes still match; fall back
    to hashing JPEG bytes if OpenCV is unavailable.
    """
    if not data:
        return "empty"
    try:
        import cv2
        import numpy as np

        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        if img is not None and img.size > 0:
            small = cv2.resize(img, (thumb, thumb), interpolation=cv2.INTER_AREA)
            return hashlib.sha1(small.tobytes()).hexdigest()[:12]
    except Exception:
        pass
    return hashlib.sha1(data).hexdigest()[:12]


class FrameChangeTracker:
    """Detect runs of identical content hashes across consecutive received frames."""

    def __init__(self, *, warn_after: int = 5) -> None:
        self.warn_after = max(2, warn_after)
        self.last_hash: str | None = None
        self.identical_run = 0
        self.total = 0
        self.unique_hashes: set[str] = set()

    def observe(self, diag: FrameDiag) -> None:
        self.total += 1
        self.unique_hashes.add(diag.content_hash)
        logger.info(
            "frame.received seq=%s hash=%s size=%s %sx%s",
            diag.sequence,
            diag.content_hash,
            diag.byte_size,
            diag.width or "?",
            diag.height or "?",
        )
        if self.last_hash is not None and diag.content_hash == self.last_hash:
            self.identical_run += 1
            if self.identical_run >= self.warn_after:
                logger.warning(
                    "frame.stale_hash seq=%s hash=%s identical_run=%s "
                    "(decoded content is not changing — check RTSP/FFmpeg)",
                    diag.sequence,
                    diag.content_hash,
                    self.identical_run,
                )
        else:
            self.identical_run = 1
            self.last_hash = diag.content_hash
