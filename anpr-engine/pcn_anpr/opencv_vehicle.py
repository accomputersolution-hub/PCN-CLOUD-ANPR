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

    def __init__(
        self,
        *,
        min_area_ratio: float = 0.02,
        max_candidates: int = 8,
    ) -> None:
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
        # Dual threshold helps night scenes (bright plates on dark bodies).
        edges_a = cv2.Canny(blur, 40, 120)
        edges_b = cv2.Canny(blur, 80, 180)
        edges = cv2.bitwise_or(edges_a, edges_b)
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
            # Cars (wide) and motorcycle rears (taller) both allowed.
            if aspect < 0.35 or aspect > 4.5:
                continue
            # Reject near-full-frame noise blobs unless nothing else exists later.
            if area >= h * w * 0.92:
                continue
            conf = min(0.85, 0.30 + area / (h * w))
            # Prefer lower/mid road band (visibility of rears).
            cy = (y + bh * 0.5) / max(h, 1)
            if 0.35 <= cy <= 0.92:
                conf = min(0.9, conf + 0.05)
            candidates.append(
                VehicleDetection(
                    bbox=BoundingBox(float(x), float(y), float(bw), float(bh), conf),
                    label="vehicle",
                    confidence=conf,
                )
            )

        # NMS-lite: drop heavy overlaps keeping higher confidence.
        candidates.sort(key=lambda v: v.confidence, reverse=True)
        kept: list[VehicleDetection] = []
        for cand in candidates:
            cx1, cy1 = cand.bbox.x, cand.bbox.y
            cx2, cy2 = cand.bbox.x + cand.bbox.w, cand.bbox.y + cand.bbox.h
            overlap = False
            for k in kept:
                kx1, ky1 = k.bbox.x, k.bbox.y
                kx2, ky2 = k.bbox.x + k.bbox.w, k.bbox.y + k.bbox.h
                ix1, iy1 = max(cx1, kx1), max(cy1, ky1)
                ix2, iy2 = min(cx2, kx2), min(cy2, ky2)
                inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
                union = cand.bbox.w * cand.bbox.h + k.bbox.w * k.bbox.h - inter
                if union > 0 and inter / union > 0.55:
                    overlap = True
                    break
            if not overlap:
                kept.append(cand)
            if len(kept) >= self.max_candidates:
                break

        if not kept:
            return [self._full_frame(frame, confidence=0.4)]
        return kept

    def _full_frame(self, frame: Any, confidence: float = 0.5) -> VehicleDetection:
        h = int(getattr(frame, "shape", [0, 0])[0] or 0)
        w = int(getattr(frame, "shape", [0, 0])[1] or 0)
        return VehicleDetection(
            bbox=BoundingBox(0.0, 0.0, float(w), float(h), confidence),
            label="vehicle",
            confidence=confidence,
        )
