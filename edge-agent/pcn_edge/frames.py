from __future__ import annotations

"""Frame source abstractions and selective snapshot saving for the edge agent.

Default path: RTSP → in-memory frame → ANPR hook → save JPEG only on detection.
Optional: DEBUG_SAVE_ALL_FRAMES=true restores continuous debug JPEG dumps.
"""

import abc
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)


@dataclass
class Frame:
    """A single captured video frame (JPEG bytes kept in memory)."""

    camera_id: str
    data: bytes
    captured_at: datetime
    width: int | None = None
    height: int | None = None
    sequence: int = 0
    content_hash: str | None = None


@dataclass
class CameraHealth:
    camera_id: str
    status: str  # ONLINE | OFFLINE | CONNECTING | ERROR
    last_frame_at: datetime | None = None
    fps: float | None = None
    reconnect_count: int = 0
    last_error: str | None = None
    resolution: str | None = None

    def to_heartbeat(self) -> dict:
        return {
            "id": self.camera_id,
            "status": self.status,
            "fps": self.fps,
            "retry_count": self.reconnect_count,
            "connection_error": self.last_error,
            "last_frame_at": self.last_frame_at.isoformat() if self.last_frame_at else None,
        }


class FrameSource(abc.ABC):
    @abc.abstractmethod
    def open(self) -> None:
        ...

    @abc.abstractmethod
    def close(self) -> None:
        ...

    @abc.abstractmethod
    def read(self) -> Frame | None:
        ...

    @property
    @abc.abstractmethod
    def is_open(self) -> bool:
        ...


class FrameProcessor(abc.ABC):
    """Consumes frames. Prefer ANPRSnapshotProcessor (selective save)."""

    @abc.abstractmethod
    def process(self, frame: Frame) -> None:
        ...


@dataclass
class MockRTSPFrameSource(FrameSource):
    """Synthetic JPEG frames for development without a real camera."""

    camera_id: str
    interval: float = 0.2
    _open: bool = False
    _seq: int = 0
    _last: float = 0.0

    def open(self) -> None:
        self._open = True
        self._last = 0.0

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def read(self) -> Frame | None:
        if not self._open:
            return None
        now = time.monotonic()
        if self._last and (now - self._last) < self.interval:
            time.sleep(max(0.0, self.interval - (now - self._last)))
        self._last = time.monotonic()
        self._seq += 1
        jpeg = (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
            b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e"
            b"\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342"
            b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
            b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
            b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00"
            + bytes([self._seq % 251])
            + b"\xff\xd9"
        )
        from pcn_edge.frame_diag import content_hash_jpeg

        return Frame(
            camera_id=self.camera_id,
            data=jpeg,
            captured_at=datetime.now(UTC),
            width=1,
            height=1,
            sequence=self._seq,
            content_hash=content_hash_jpeg(jpeg),
        )


@dataclass
class _RingEntry:
    frame: Frame
    score: float = 0.0


class FrameRingBuffer:
    """Small in-memory ring of recent frames for best-snapshot selection."""

    def __init__(self, maxlen: int = 15) -> None:
        self._buf: deque[_RingEntry] = deque(maxlen=max(1, maxlen))

    def push(self, frame: Frame, score: float = 0.0) -> None:
        self._buf.append(_RingEntry(frame=frame, score=score))

    def update_latest_score(self, score: float) -> None:
        if self._buf:
            self._buf[-1].score = score

    def best(self) -> Frame | None:
        if not self._buf:
            return None
        return max(self._buf, key=lambda e: (e.score, e.frame.sequence)).frame

    def latest(self) -> Frame | None:
        return self._buf[-1].frame if self._buf else None

    def __len__(self) -> int:
        return len(self._buf)


@dataclass
class DebugFrameProcessor(FrameProcessor):
    """Legacy/debug: persist sampled JPEGs regardless of ANPR (DEBUG_SAVE_ALL_FRAMES)."""

    save_dir: str
    save_every_n: int = 1
    max_files: int = 200
    _counts: dict[str, int] = field(default_factory=dict)

    def process(self, frame: Frame) -> None:
        self._counts[frame.camera_id] = self._counts.get(frame.camera_id, 0) + 1
        n = self._counts[frame.camera_id]
        if self.save_every_n > 1 and n != 1 and n % self.save_every_n != 1:
            return
        _write_snapshot(self.save_dir, frame, reason="debug_all", max_files=self.max_files)


AnprHook = Callable[[Frame], dict[str, Any]]


def default_anpr_hook(frame: Frame) -> dict[str, Any]:
    """Run Phase 6A ANPR on an in-memory JPEG. Never creates DB events."""
    empty = {
        "vehicle_detected": False,
        "plate_detected": False,
        "has_ocr_text": False,
        "meaningful_vehicle": False,
        "score": 0.0,
        "plates": [],
        "error": None,
    }
    try:
        import cv2
        import numpy as np
    except ImportError:
        empty["error"] = "opencv_unavailable"
        return empty

    try:
        arr = np.frombuffer(frame.data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            empty["error"] = "decode_failed"
            return empty

        from pcn_anpr.factory import build_pipeline

        pipeline = default_anpr_hook._pipeline  # type: ignore[attr-defined]
        if pipeline is None:
            pipeline = build_pipeline()
            default_anpr_hook._pipeline = pipeline  # type: ignore[attr-defined]

        payload = pipeline._infer(img)  # structured result, no disk I/O
        plates = payload.get("plates") or []
        vehicles = payload.get("vehicles") or []
        plate_detected = bool(payload.get("plate_detected"))
        has_ocr = any(bool((p.get("raw_text") or "").strip()) for p in plates)
        meaningful_vehicle = _meaningful_vehicle(vehicles, img.shape[1], img.shape[0])
        score = 0.0
        if plates:
            score = max(float(p.get("confidence") or 0.0) for p in plates)
        elif meaningful_vehicle:
            score = max(float(v.get("confidence") or 0.0) for v in vehicles) * 0.5
        return {
            "vehicle_detected": bool(payload.get("vehicle_detected")),
            "plate_detected": plate_detected,
            "has_ocr_text": has_ocr,
            "meaningful_vehicle": meaningful_vehicle,
            "score": score,
            "plates": plates,
            "error": payload.get("error"),
        }
    except Exception as exc:  # noqa: BLE001 — never crash capture loop
        empty["error"] = str(exc)[:300]
        return empty


default_anpr_hook._pipeline = None  # type: ignore[attr-defined]


def _meaningful_vehicle(vehicles: list[dict[str, Any]], width: int, height: int) -> bool:
    """Ignore full-frame 'scene' fallbacks so we do not save every frame."""
    frame_area = max(1, width * height)
    for v in vehicles:
        bb = v.get("bbox") or []
        if len(bb) != 4:
            continue
        x1, y1, x2, y2 = bb
        area = max(0, (x2 - x1) * (y2 - y1))
        ratio = area / frame_area
        # Contour vehicle occupying less than ~90% of the frame
        if 0.02 <= ratio <= 0.90:
            return True
    return False


def should_save_snapshot(anpr: dict[str, Any], *, debug_save_all: bool = False) -> bool:
    if debug_save_all:
        return True
    return bool(
        anpr.get("plate_detected")
        or anpr.get("has_ocr_text")
        or anpr.get("meaningful_vehicle")
    )


@dataclass
class ANPRSnapshotProcessor(FrameProcessor):
    """RTSP frames stay in memory; JPEG written only for detections (or debug-all)."""

    save_dir: str
    debug_save_all: bool = False
    ring_size: int = 15
    max_files: int = 100
    anpr_hook: AnprHook | None = None
    anpr_enabled: bool = True
    _rings: dict[str, FrameRingBuffer] = field(default_factory=dict)
    _saved: int = 0
    _discarded: int = 0

    def process(self, frame: Frame) -> None:
        ring = self._rings.setdefault(frame.camera_id, FrameRingBuffer(self.ring_size))
        ring.push(frame, score=0.0)

        if self.debug_save_all:
            _write_snapshot(self.save_dir, frame, reason="debug_all", max_files=self.max_files)
            self._saved += 1
            return

        if not self.anpr_enabled:
            self._discarded += 1
            return

        hook = self.anpr_hook or default_anpr_hook
        anpr = hook(frame)
        score = float(anpr.get("score") or 0.0)
        ring.update_latest_score(score)

        if not should_save_snapshot(anpr, debug_save_all=False):
            self._discarded += 1
            logger.debug(
                "frame.discarded camera=%s seq=%s plate=%s vehicle=%s",
                frame.camera_id,
                frame.sequence,
                anpr.get("plate_detected"),
                anpr.get("meaningful_vehicle"),
            )
            return

        best = ring.best() or frame
        reason = "plate" if anpr.get("plate_detected") or anpr.get("has_ocr_text") else "vehicle"
        path = _write_snapshot(self.save_dir, best, reason=reason, max_files=self.max_files)
        self._saved += 1
        logger.info(
            "snapshot.saved camera=%s seq=%s reason=%s path=%s score=%.3f",
            best.camera_id,
            best.sequence,
            reason,
            path,
            score,
        )


def _write_snapshot(save_dir: str, frame: Frame, *, reason: str, max_files: int) -> Path:
    root = Path(save_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{frame.camera_id}_{frame.sequence}_{reason}.jpg"
    path.write_bytes(frame.data)
    files = sorted(root.glob(f"{frame.camera_id}_*.jpg"), key=lambda p: p.stat().st_mtime)
    for old in files[: max(0, len(files) - max_files)]:
        try:
            old.unlink()
        except OSError:
            pass
    return path


def iter_frames(source: FrameSource, *, max_frames: int | None = None) -> Iterator[Frame]:
    count = 0
    while True:
        frame = source.read()
        if frame is None:
            break
        yield frame
        count += 1
        if max_frames is not None and count >= max_frames:
            break
