from __future__ import annotations

"""OpenCV-based vehicle region proposal (Apache 2.0 OpenCV — not AGPL).

This is a lightweight heuristic detector (edges / large contours), not a neural net.
It is replaceable via VehicleDetector. Suitable for Phase 6A until a commercially
licensed detector is plugged in.
"""

from typing import Any

from pcn_anpr.interfaces import BoundingBox, VehicleDetection, VehicleDetector


class OpenCVVehicleDetector(VehicleDetector):
    """Propose vehicle-sized regions; always falls back to full-frame if none found."""

    def __init__(self, *, min_area_ratio: float = 0.05, max_candidates: int = 5) -> None:
        self.min_area_ratio = min_area_ratio
        self.max_candidates = max_candidates

    def detect(self, frame: Any) -> list[VehicleDetection]:
        if frame is None:
            return []
        try:
            import cv2
            import numpy as np
        except ImportError:
            return [self._full_frame(frame)]

        if not hasattr(frame, "shape") or len(frame.shape) < 2:
            return []

        h, w = frame.shape[:2]
        if h < 16 or w < 16:
            return [self._full_frame(frame, confidence=0.2)]

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 50, 150)
        edges = cv2.dilate(edges, np.ones((5, 5), np.uint8), iterations=2)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        min_area = h * w * self.min_area_ratio
        candidates: list[VehicleDetection] = []
        for cnt in contours:
            x, y, bw, bh = cv2.boundingRect(cnt)
            area = bw * bh
            if area < min_area:
                continue
            aspect = bw / max(bh, 1)
            if aspect < 0.6 or aspect > 4.0:
                continue
            conf = min(0.85, 0.35 + area / (h * w))
            candidates.append(
                VehicleDetection(
                    bbox=BoundingBox(float(x), float(y), float(bw), float(bh), conf),
                    label="vehicle",
                    confidence=conf,
                )
            )

        candidates.sort(key=lambda v: v.confidence, reverse=True)
        if not candidates:
            return [self._full_frame(frame, confidence=0.4)]
        return candidates[: self.max_candidates]

    def _full_frame(self, frame: Any, confidence: float = 0.5) -> VehicleDetection:
        h = int(getattr(frame, "shape", [0, 0])[0] or 0)
        w = int(getattr(frame, "shape", [0, 0])[1] or 0)
        return VehicleDetection(
            bbox=BoundingBox(0.0, 0.0, float(w), float(h), confidence),
            label="vehicle",
            confidence=confidence,
        )
