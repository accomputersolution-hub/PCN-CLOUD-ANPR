from __future__ import annotations

"""Per-vehicle track IDs, OCR history, and temporal confirmation.

Manual ANPR is single-frame (track IDs are frame-local). Live ANPR keeps tracks
alive across frames and confirms plates per track so one vehicle never overwrites
another.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Sequence

from pcn_anpr.interfaces import VehicleDetection
from pcn_anpr.vehicle_assoc import _iou, _xyxy


@dataclass
class TrackObservation:
    """One OCR observation attributed to a vehicle track."""

    track_id: str
    plate_normalized: str
    plate_raw: str
    ocr_confidence: float
    plate_confidence: float
    timestamp: datetime
    on_primary: bool = False
    plate_bbox: list[int] = field(default_factory=list)
    vehicle_bbox: list[int] = field(default_factory=list)
    frame_sequence: int = 0
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class TrackState:
    track_id: str
    bbox_xyxy: tuple[float, float, float, float]
    confidence: float
    hits: int = 1
    misses: int = 0
    last_seen: datetime | None = None
    is_primary: bool = False
    best_plate: str | None = None
    best_ocr_confidence: float = 0.0
    ocr_history: deque[TrackObservation] = field(default_factory=lambda: deque(maxlen=32))


@dataclass
class ConfirmedTrackPlate:
    track_id: str
    plate_normalized: str
    plate_raw: str
    ocr_confidence: float
    plate_confidence: float
    observation_count: int
    timestamp: datetime
    on_primary: bool
    plate_bbox: list[int]
    vehicle_bbox: list[int]
    frame_sequence: int


def _bbox_xyxy(v: VehicleDetection) -> tuple[float, float, float, float]:
    return _xyxy(v.bbox)


class VehicleTracker:
    """IoU-based multi-vehicle tracker with per-track OCR history."""

    def __init__(
        self,
        *,
        iou_match: float = 0.25,
        max_misses: int = 8,
        min_confirm_observations: int = 3,
        confirm_window_seconds: float = 2.0,
        min_ocr_confidence: float = 0.55,
    ) -> None:
        self.iou_match = iou_match
        self.max_misses = max_misses
        self.min_confirm_observations = max(1, min_confirm_observations)
        self.confirm_window = timedelta(seconds=max(0.1, confirm_window_seconds))
        self.min_ocr_confidence = min_ocr_confidence
        self._tracks: dict[str, TrackState] = {}
        self._next_id = 1

    def reset(self) -> None:
        self._tracks.clear()
        self._next_id = 1

    @property
    def tracks(self) -> dict[str, TrackState]:
        return self._tracks

    def update(
        self,
        detections: Sequence[VehicleDetection],
        *,
        primary_index: int | None = None,
        now: datetime | None = None,
    ) -> list[VehicleDetection]:
        """Match detections to tracks; return detections with ``track_id`` set."""
        now = now or datetime.now(UTC)
        if not detections:
            for tr in self._tracks.values():
                tr.misses += 1
            self._prune()
            return []

        track_ids = list(self._tracks.keys())
        used_tracks: set[str] = set()
        used_dets: set[int] = set()
        pairs: list[tuple[float, str, int]] = []
        for tid in track_ids:
            tr = self._tracks[tid]
            for i, det in enumerate(detections):
                iou = _iou(tr.bbox_xyxy, _bbox_xyxy(det))
                if iou >= self.iou_match:
                    pairs.append((iou, tid, i))
        pairs.sort(reverse=True)

        assigned: list[VehicleDetection] = [None] * len(detections)  # type: ignore[list-item]
        for iou, tid, i in pairs:
            if tid in used_tracks or i in used_dets:
                continue
            used_tracks.add(tid)
            used_dets.add(i)
            det = detections[i]
            tr = self._tracks[tid]
            xyxy = _bbox_xyxy(det)
            tr.bbox_xyxy = xyxy
            tr.confidence = float(det.confidence)
            tr.hits += 1
            tr.misses = 0
            tr.last_seen = now
            tr.is_primary = bool(primary_index is not None and i == primary_index)
            tagged = VehicleDetection(
                bbox=det.bbox,
                label=det.label,
                confidence=det.confidence,
                track_id=tid,
            )
            assigned[i] = tagged

        for i, det in enumerate(detections):
            if i in used_dets:
                continue
            tid = f"v{self._next_id}"
            self._next_id += 1
            xyxy = _bbox_xyxy(det)
            self._tracks[tid] = TrackState(
                track_id=tid,
                bbox_xyxy=xyxy,
                confidence=float(det.confidence),
                hits=1,
                misses=0,
                last_seen=now,
                is_primary=bool(primary_index is not None and i == primary_index),
            )
            assigned[i] = VehicleDetection(
                bbox=det.bbox,
                label=det.label,
                confidence=det.confidence,
                track_id=tid,
            )

        for tid, tr in list(self._tracks.items()):
            if tid not in used_tracks and tid not in {d.track_id for d in assigned if d}:
                tr.misses += 1
        self._prune()
        return [d for d in assigned if d is not None]

    def _prune(self) -> None:
        dead = [tid for tid, tr in self._tracks.items() if tr.misses > self.max_misses]
        for tid in dead:
            del self._tracks[tid]

    def note_plate(
        self,
        observation: TrackObservation,
    ) -> ConfirmedTrackPlate | None:
        """Record OCR for a track; never merges history across tracks.

        Returns a confirmation when the same plate is seen enough times on
        *this* track within the confirmation window.
        """
        if not observation.plate_normalized or not observation.track_id:
            return None
        tr = self._tracks.get(observation.track_id)
        if tr is None:
            # Keep orphan observation under a soft track so live history survives
            # a brief detection gap.
            tr = TrackState(
                track_id=observation.track_id,
                bbox_xyxy=tuple(float(x) for x in (observation.vehicle_bbox or [0, 0, 1, 1])[:4])
                if len(observation.vehicle_bbox or []) >= 4
                else (0.0, 0.0, 1.0, 1.0),
                confidence=0.0,
                misses=0,
                last_seen=observation.timestamp,
                is_primary=observation.on_primary,
            )
            if len(observation.vehicle_bbox or []) >= 4:
                b = observation.vehicle_bbox
                tr.bbox_xyxy = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
            self._tracks[observation.track_id] = tr

        tr.ocr_history.append(observation)
        tr.last_seen = observation.timestamp
        tr.misses = 0
        if observation.on_primary:
            tr.is_primary = True
        if float(observation.ocr_confidence) >= float(tr.best_ocr_confidence):
            tr.best_plate = observation.plate_normalized
            tr.best_ocr_confidence = float(observation.ocr_confidence)

        if observation.ocr_confidence < self.min_ocr_confidence:
            return None

        cutoff = observation.timestamp - self.confirm_window
        matching = [
            o
            for o in tr.ocr_history
            if o.plate_normalized == observation.plate_normalized
            and o.timestamp >= cutoff
            and o.ocr_confidence >= self.min_ocr_confidence
        ]
        if len(matching) < self.min_confirm_observations:
            return None

        avg_ocr = sum(o.ocr_confidence for o in matching) / len(matching)
        best = max(matching, key=lambda o: (o.ocr_confidence, o.plate_confidence))
        # Consume matching obs so the same streak does not re-fire immediately.
        remain = deque(
            (o for o in tr.ocr_history if o.plate_normalized != observation.plate_normalized),
            maxlen=tr.ocr_history.maxlen,
        )
        tr.ocr_history = remain
        return ConfirmedTrackPlate(
            track_id=tr.track_id,
            plate_normalized=observation.plate_normalized,
            plate_raw=best.plate_raw,
            ocr_confidence=avg_ocr,
            plate_confidence=sum(o.plate_confidence for o in matching) / len(matching),
            observation_count=len(matching),
            timestamp=observation.timestamp,
            on_primary=bool(tr.is_primary or any(o.on_primary for o in matching)),
            plate_bbox=list(best.plate_bbox),
            vehicle_bbox=list(best.vehicle_bbox),
            frame_sequence=best.frame_sequence,
        )

    def snapshot(self) -> list[dict[str, Any]]:
        """Serialize active tracks for pipeline / live diagnostics."""
        rows: list[dict[str, Any]] = []
        for tr in sorted(self._tracks.values(), key=lambda t: (-int(t.is_primary), -t.hits)):
            rows.append(
                {
                    "track_id": tr.track_id,
                    "bbox": [int(v) for v in tr.bbox_xyxy],
                    "confidence": round(tr.confidence, 4),
                    "hits": tr.hits,
                    "misses": tr.misses,
                    "is_primary": tr.is_primary,
                    "best_plate": tr.best_plate,
                    "best_ocr_confidence": round(tr.best_ocr_confidence, 4),
                    "ocr_history_len": len(tr.ocr_history),
                }
            )
        return rows


def assign_frame_local_track_ids(
    vehicles: Sequence[VehicleDetection],
) -> list[VehicleDetection]:
    """Stable within a single Manual ANPR frame (no cross-frame state)."""
    out: list[VehicleDetection] = []
    for i, v in enumerate(vehicles):
        tid = v.track_id or f"v{i + 1}"
        out.append(
            VehicleDetection(
                bbox=v.bbox,
                label=v.label,
                confidence=v.confidence,
                track_id=tid,
            )
        )
    return out
