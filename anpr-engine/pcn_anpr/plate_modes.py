from __future__ import annotations

"""Composite plate-detector modes: OpenCV default, AI+fallback, compare."""

import logging
import time
from typing import Any

from pcn_anpr.interfaces import PlateDetection, PlateDetector, VehicleDetection

logger = logging.getLogger(__name__)


def _summarize(plates: list[PlateDetection]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in plates:
        rows.append(
            {
                "bbox": [p.bbox.x, p.bbox.y, p.bbox.x + p.bbox.w, p.bbox.y + p.bbox.h],
                "confidence": float(p.confidence),
                "class_name": getattr(p, "class_name", None) or "license_plate",
                "timing_ms": getattr(p, "timing_ms", None),
            }
        )
    return rows


class TimedPlateDetector(PlateDetector):
    """Thin adapter that records wall time on the best detection / last_meta."""

    def __init__(self, inner: PlateDetector, *, name: str = "detector") -> None:
        self.inner = inner
        self.name = name
        self.last_time_ms: float = 0.0
        self.last_detections: list[PlateDetection] = []

    def initialize(self) -> bool:
        if hasattr(self.inner, "initialize"):
            return bool(self.inner.initialize())
        return True

    def warm_up(self) -> dict[str, Any]:
        if hasattr(self.inner, "warm_up"):
            return dict(self.inner.warm_up())
        return {"ok": self.initialize()}

    def detect(self, frame: Any, vehicle: VehicleDetection | None = None) -> list[PlateDetection]:
        started = time.perf_counter()
        dets = list(self.inner.detect(frame, vehicle) or [])
        self.last_time_ms = (time.perf_counter() - started) * 1000.0
        # Stamp timing on detections that lack it (e.g. OpenCV).
        stamped: list[PlateDetection] = []
        for p in dets:
            if p.timing_ms is None:
                stamped.append(
                    PlateDetection(
                        bbox=p.bbox,
                        confidence=p.confidence,
                        class_name=getattr(p, "class_name", None) or "license_plate",
                        timing_ms=self.last_time_ms,
                    )
                )
            else:
                stamped.append(p)
        self.last_detections = stamped
        return stamped


class AIWithOpenCVFallback(PlateDetector):
    """AI primary; OpenCV fallback when AI returns no detections (keeps pipeline alive)."""

    def __init__(self, ai: PlateDetector, opencv: PlateDetector) -> None:
        self.ai = TimedPlateDetector(ai, name="ai")
        self.opencv = TimedPlateDetector(opencv, name="opencv")
        self.last_meta: dict[str, Any] = {}

    def initialize(self) -> bool:
        ai_ok = self.ai.initialize()
        cv_ok = self.opencv.initialize()
        return ai_ok or cv_ok

    def warm_up(self) -> dict[str, Any]:
        return {"ai": self.ai.warm_up(), "opencv": self.opencv.warm_up()}

    def detect(self, frame: Any, vehicle: VehicleDetection | None = None) -> list[PlateDetection]:
        ai_dets = self.ai.detect(frame, vehicle)
        used = "ai"
        dets = ai_dets
        if not ai_dets:
            used = "opencv_fallback"
            dets = self.opencv.detect(frame, vehicle)
        self.last_meta = {
            "mode": "ai",
            "used": used,
            "ai_count": len(ai_dets),
            "ai_time_ms": self.ai.last_time_ms,
            "ai_detections": _summarize(ai_dets),
            "opencv_time_ms": self.opencv.last_time_ms if used == "opencv_fallback" else None,
            "opencv_detections": _summarize(self.opencv.last_detections)
            if used == "opencv_fallback"
            else [],
        }
        if used == "opencv_fallback":
            logger.warning(
                "plate.ai.empty_fallback_opencv ai_ms=%.2f opencv_ms=%.2f n=%s "
                "(AI weights not loaded or inference disabled — not claiming AI detection)",
                self.ai.last_time_ms,
                self.opencv.last_time_ms,
                len(dets),
            )
        return dets


class ComparePlateDetector(PlateDetector):
    """Evaluation-only: run both detectors, log both, return OpenCV for production OCR."""

    def __init__(self, opencv: PlateDetector, ai: PlateDetector) -> None:
        self.opencv = TimedPlateDetector(opencv, name="opencv")
        self.ai = TimedPlateDetector(ai, name="ai")
        self.last_meta: dict[str, Any] = {}

    def initialize(self) -> bool:
        cv_ok = self.opencv.initialize()
        self.ai.initialize()
        return cv_ok

    def warm_up(self) -> dict[str, Any]:
        return {"opencv": self.opencv.warm_up(), "ai": self.ai.warm_up()}

    def detect(self, frame: Any, vehicle: VehicleDetection | None = None) -> list[PlateDetection]:
        opencv_dets = self.opencv.detect(frame, vehicle)
        ai_dets = self.ai.detect(frame, vehicle)
        self.last_meta = {
            "mode": "compare",
            "used": "opencv",
            "opencv_time_ms": self.opencv.last_time_ms,
            "ai_time_ms": self.ai.last_time_ms,
            "opencv_detections": _summarize(opencv_dets),
            "ai_detections": _summarize(ai_dets),
            "opencv_count": len(opencv_dets),
            "ai_count": len(ai_dets),
        }
        logger.info(
            "plate.compare opencv_n=%s opencv_ms=%.2f opencv=%s ai_n=%s ai_ms=%.2f ai=%s",
            len(opencv_dets),
            self.opencv.last_time_ms,
            _summarize(opencv_dets[:3]),
            len(ai_dets),
            self.ai.last_time_ms,
            _summarize(ai_dets[:3]),
        )
        # Production path: OpenCV only — no duplicate events from AI boxes.
        return opencv_dets
