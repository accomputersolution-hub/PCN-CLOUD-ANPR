from __future__ import annotations

"""OpenCV morphological license-plate detector (OpenCV Apache 2.0 — not AGPL).

This is a dedicated plate-region proposal step. OCR is NOT run on the full image
by this class — it only returns rectangular plate candidates for cropping.

Replaceable via PlateDetector when a commercially licensed neural detector is available.
Model weights: none required (classical CV). Optional future weights go under ANPR_MODEL_DIR.
"""

from typing import Any

from pcn_anpr.interfaces import BoundingBox, PlateDetection, PlateDetector, VehicleDetection


class OpenCVPlateDetector(PlateDetector):
    """Find plate-like rectangles using edges + morphology + aspect-ratio filters."""

    def __init__(
        self,
        *,
        min_aspect: float = 1.8,
        max_aspect: float = 7.5,
        min_area_ratio: float = 0.0015,
        max_area_ratio: float = 0.25,
        max_candidates: int = 8,
        min_confidence: float = 0.2,
    ) -> None:
        self.min_aspect = min_aspect
        self.max_aspect = max_aspect
        self.min_area_ratio = min_area_ratio
        self.max_area_ratio = max_area_ratio
        self.max_candidates = max_candidates
        self.min_confidence = min_confidence

    def detect(self, frame: Any, vehicle: VehicleDetection | None = None) -> list[PlateDetection]:
        if frame is None:
            return []
        try:
            import cv2
            import numpy as np
        except ImportError:
            return []

        if not hasattr(frame, "shape") or len(frame.shape) < 2:
            return []

        roi, ox, oy = self._roi(frame, vehicle)
        rh, rw = roi.shape[:2]
        if rh < 20 or rw < 40:
            return []

        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
        gray = cv2.bilateralFilter(gray, 7, 40, 40)
        # Black-hat / morph to emphasize plate-like high-contrast rectangles
        rect_kern = cv2.getStructuringElement(cv2.MORPH_RECT, (17, 5))
        blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, rect_kern)
        grad = cv2.Sobel(blackhat, cv2.CV_32F, 1, 0, ksize=3)
        grad = np.absolute(grad)
        grad = np.uint8(255 * (grad / (grad.max() + 1e-6)))
        _, thresh = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
        thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, rect_kern)
        thresh = cv2.erode(thresh, None, iterations=1)
        thresh = cv2.dilate(thresh, None, iterations=2)

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        frame_area = float(frame.shape[0] * frame.shape[1])
        candidates: list[PlateDetection] = []

        for cnt in contours:
            x, y, bw, bh = cv2.boundingRect(cnt)
            if bh < 8 or bw < 24:
                continue
            aspect = bw / max(bh, 1)
            if aspect < self.min_aspect or aspect > self.max_aspect:
                continue
            area = float(bw * bh)
            area_ratio = area / max(frame_area, 1.0)
            if area_ratio < self.min_area_ratio or area_ratio > self.max_area_ratio:
                continue
            # Prefer mid-image plates slightly
            cy = (y + bh / 2) / max(rh, 1)
            center_bonus = 0.1 if 0.25 <= cy <= 0.9 else 0.0
            aspect_score = 1.0 - abs(aspect - 4.0) / 4.0
            conf = float(max(0.0, min(0.95, 0.35 + 0.35 * aspect_score + center_bonus)))
            if conf < self.min_confidence:
                continue
            candidates.append(
                PlateDetection(
                    bbox=BoundingBox(
                        float(x + ox),
                        float(y + oy),
                        float(bw),
                        float(bh),
                        conf,
                    ),
                    confidence=conf,
                )
            )

        candidates.sort(key=lambda p: p.confidence, reverse=True)
        # Deduplicate heavily overlapping boxes
        return self._nms(candidates)[: self.max_candidates]

    def _roi(self, frame: Any, vehicle: VehicleDetection | None) -> tuple[Any, int, int]:
        if vehicle is None:
            return frame, 0, 0
        b = vehicle.bbox
        x1, y1 = int(b.x), int(b.y)
        x2, y2 = int(b.x + b.w), int(b.y + b.h)
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 - x1 < 40 or y2 - y1 < 20:
            return frame, 0, 0
        return frame[y1:y2, x1:x2], x1, y1

    def _nms(self, plates: list[PlateDetection], iou_thresh: float = 0.35) -> list[PlateDetection]:
        kept: list[PlateDetection] = []
        for p in plates:
            if all(self._iou(p, k) < iou_thresh for k in kept):
                kept.append(p)
        return kept

    def _iou(self, a: PlateDetection, b: PlateDetection) -> float:
        ax1, ay1 = a.bbox.x, a.bbox.y
        ax2, ay2 = a.bbox.x + a.bbox.w, a.bbox.y + a.bbox.h
        bx1, by1 = b.bbox.x, b.bbox.y
        bx2, by2 = b.bbox.x + b.bbox.w, b.bbox.y + b.bbox.h
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
        inter = iw * ih
        if inter <= 0:
            return 0.0
        union = a.bbox.w * a.bbox.h + b.bbox.w * b.bbox.h - inter
        return inter / union if union > 0 else 0.0
