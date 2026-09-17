from __future__ import annotations

"""Temporal plate confirmation and per-camera cooldown (Phase 6B).

Does not invent characters. Only confirms when enough consistent, confident
observations of the same normalized plate arrive within a time window.
"""

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any


@dataclass
class PlateObservation:
    camera_id: str
    plate_normalized: str
    plate_raw: str
    ocr_confidence: float
    plate_confidence: float
    vehicle_confidence: float
    timestamp: datetime
    frame_jpeg: bytes
    frame_sequence: int
    plate_bbox: list[int] = field(default_factory=list)
    padded_bbox: list[int] = field(default_factory=list)
    vehicle_bbox: list[int] = field(default_factory=list)
    processing_ms: int = 0
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConfirmedPlate:
    camera_id: str
    plate_normalized: str
    plate_raw: str
    ocr_confidence: float
    plate_confidence: float
    vehicle_confidence: float
    observation_count: int
    timestamp: datetime
    best_frame_jpeg: bytes
    frame_sequence: int
    plate_bbox: list[int]
    padded_bbox: list[int]
    vehicle_bbox: list[int]
    processing_ms: int


class TemporalPlateTracker:
    """Per-camera rolling window of OCR observations → confirmed plates."""

    def __init__(
        self,
        *,
        min_observations: int = 3,
        window_seconds: float = 2.0,
        min_ocr_confidence: float = 0.7,
    ) -> None:
        self.min_observations = max(1, min_observations)
        self.window = timedelta(seconds=max(0.1, window_seconds))
        self.min_ocr_confidence = min_ocr_confidence
        self._obs: dict[str, deque[PlateObservation]] = defaultdict(deque)

    def add(self, observation: PlateObservation) -> ConfirmedPlate | None:
        if not observation.plate_normalized:
            return None
        if observation.ocr_confidence < self.min_ocr_confidence:
            return None

        buf = self._obs[observation.camera_id]
        buf.append(observation)
        self._prune(observation.camera_id, observation.timestamp)

        plate = observation.plate_normalized
        matching = [o for o in buf if o.plate_normalized == plate]
        if len(matching) < self.min_observations:
            return None

        avg_ocr = sum(o.ocr_confidence for o in matching) / len(matching)
        if avg_ocr < self.min_ocr_confidence:
            return None

        # Consistency: all matching obs must share the same normalized plate (already filtered).
        # Prefer highest OCR confidence frame as snapshot source.
        best = max(matching, key=lambda o: (o.ocr_confidence, o.plate_confidence))
        confirmed = ConfirmedPlate(
            camera_id=observation.camera_id,
            plate_normalized=plate,
            plate_raw=best.plate_raw,
            ocr_confidence=avg_ocr,
            plate_confidence=sum(o.plate_confidence for o in matching) / len(matching),
            vehicle_confidence=sum(o.vehicle_confidence for o in matching) / len(matching),
            observation_count=len(matching),
            timestamp=observation.timestamp,
            best_frame_jpeg=best.frame_jpeg,
            frame_sequence=best.frame_sequence,
            plate_bbox=list(best.plate_bbox),
            padded_bbox=list(best.padded_bbox or best.plate_bbox),
            vehicle_bbox=list(best.vehicle_bbox),
            processing_ms=best.processing_ms,
        )
        # Clear matching observations so we don't re-confirm until new passes
        self._obs[observation.camera_id] = deque(
            o for o in buf if o.plate_normalized != plate
        )
        return confirmed

    def _prune(self, camera_id: str, now: datetime) -> None:
        buf = self._obs[camera_id]
        cutoff = now - self.window
        while buf and buf[0].timestamp < cutoff:
            buf.popleft()


class EventCoolDown:
    """Suppress duplicate events for the same camera+plate within a cooldown."""

    def __init__(self, cooldown_seconds: float = 120.0) -> None:
        self.cooldown = timedelta(seconds=max(0.0, cooldown_seconds))
        self._last: dict[tuple[str, str], datetime] = {}

    def allow(self, camera_id: str, plate_normalized: str, when: datetime | None = None) -> bool:
        when = when or datetime.now(UTC)
        key = (camera_id, plate_normalized)
        prev = self._last.get(key)
        if prev is not None and (when - prev) < self.cooldown:
            return False
        return True

    def mark(self, camera_id: str, plate_normalized: str, when: datetime | None = None) -> None:
        self._last[(camera_id, plate_normalized)] = when or datetime.now(UTC)
