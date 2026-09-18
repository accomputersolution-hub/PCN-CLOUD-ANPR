from __future__ import annotations

"""OpenCV morphological license-plate detector (OpenCV Apache 2.0 — not AGPL).

This is a dedicated plate-region proposal step. OCR is NOT run on the full image
by this class — it only returns rectangular plate candidates for cropping.

HSRP plates include a left ``IND`` legend strip that is easy to isolate as a
tiny high-contrast box. Scoring therefore prefers larger, wider candidates so
the registration field wins over the country marker alone.

Replaceable via PlateDetector when a commercially licensed neural detector is available.
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
        candidates: list[PlateDetection] = []
        candidates.extend(self._detect_morph(frame, gray, rh, rw, ox, oy))
        # Oblique / tilted HSRP plates are often missed by horizontal morph — add
        # bright rotated rectangles with glyph-like structure.
        candidates.extend(self._detect_bright_rotated(frame, roi, gray, rh, rw, ox, oy))

        # Re-score with crop quality so road/gravel cannot outrank real plates.
        from pcn_anpr.plate_quality import assess_plate_crop

        fh, fw = int(frame.shape[0]), int(frame.shape[1])
        vehicle_xyxy = None
        if vehicle is not None:
            vb = vehicle.bbox
            vehicle_xyxy = (vb.x, vb.y, vb.x + vb.w, vb.y + vb.h)

        rescored: list[PlateDetection] = []
        for p in candidates:
            x1, y1 = int(p.bbox.x), int(p.bbox.y)
            x2, y2 = int(p.bbox.x + p.bbox.w), int(p.bbox.y + p.bbox.h)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(fw, x2), min(fh, y2)
            if x2 <= x1 or y2 <= y1:
                continue
            crop = frame[y1:y2, x1:x2]
            assessment = assess_plate_crop(
                crop,
                bbox_xyxy=(float(x1), float(y1), float(x2), float(y2)),
                frame_shape=(fh, fw),
                vehicle_bbox=vehicle_xyxy,
            )
            if assessment.reject:
                continue
            conf = float(min(0.95, 0.55 * float(p.confidence) + 0.45 * assessment.score))
            if conf < self.min_confidence:
                continue
            rescored.append(
                PlateDetection(
                    bbox=BoundingBox(float(x1), float(y1), float(x2 - x1), float(y2 - y1), conf),
                    confidence=conf,
                    class_name=getattr(p, "class_name", None) or "license_plate",
                )
            )

        # If quality filter emptied the list, keep morph proposals (low conf) so OCR
        # can still attempt — but never reinstate hard-rejected road boxes above.
        if not rescored:
            for p in candidates:
                cy = (p.bbox.y + p.bbox.h * 0.5) / max(fh, 1)
                if cy >= 0.88:
                    continue
                if p.confidence < self.min_confidence:
                    continue
                rescored.append(p)

        rescored.sort(key=lambda p: (p.confidence, p.bbox.w * p.bbox.h), reverse=True)
        return self._nms(rescored)[: self.max_candidates]

    def _detect_morph(
        self, frame: Any, gray: Any, rh: int, rw: int, ox: int, oy: int
    ) -> list[PlateDetection]:
        import cv2
        import numpy as np

        candidates: list[PlateDetection] = []
        frame_area = float(frame.shape[0] * frame.shape[1])
        roi_area = float(max(rh * rw, 1))
        # Two morph scales: fine edges (can catch IND strip) + coarser (full HSRP).
        for kern_w, kern_h in ((17, 5), (31, 9)):
            rect_kern = cv2.getStructuringElement(cv2.MORPH_RECT, (kern_w, kern_h))
            blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, rect_kern)
            grad = cv2.Sobel(blackhat, cv2.CV_32F, 1, 0, ksize=3)
            grad = np.absolute(grad)
            grad = np.uint8(255 * (grad / (grad.max() + 1e-6)))
            _, thresh = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
            thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, rect_kern)
            thresh = cv2.erode(thresh, None, iterations=1)
            thresh = cv2.dilate(thresh, None, iterations=2)

            contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
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
                cy = (y + bh / 2) / max(rh, 1)
                # Prefer bumper / mid-lower vehicle band; penalize absolute bottom (road).
                if cy >= 0.92:
                    center_bonus = -0.35
                elif 0.35 <= cy <= 0.85:
                    center_bonus = 0.10
                else:
                    center_bonus = 0.0
                aspect_score = 1.0 - abs(aspect - 4.2) / 4.2
                area_score = min(1.0, area / (roi_area * 0.02))
                width_score = min(1.0, bw / max(rw * 0.25, 1.0))
                tiny_penalty = -0.28 if bw < max(90.0, rw * 0.10) else 0.0
                conf = float(
                    max(
                        0.0,
                        min(
                            0.95,
                            0.22
                            + 0.22 * aspect_score
                            + 0.28 * area_score
                            + 0.18 * width_score
                            + center_bonus
                            + tiny_penalty,
                        ),
                    )
                )
                if conf < self.min_confidence * 0.75:
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
        return candidates

    def _detect_bright_rotated(
        self, frame: Any, roi: Any, gray: Any, rh: int, rw: int, ox: int, oy: int
    ) -> list[PlateDetection]:
        """Find bright plate panels via threshold + minAreaRect (handles oblique plates)."""
        import cv2
        import numpy as np

        from pcn_anpr.plate_quality import count_char_like_blobs

        candidates: list[PlateDetection] = []
        frame_area = float(frame.shape[0] * frame.shape[1])
        # Use lightly smoothed gray — heavy bilateral can fuse plate into grille.
        gray_src = cv2.GaussianBlur(gray, (3, 3), 0)
        # Restrict to mid/lower vehicle band where Indian plates usually sit.
        y0 = int(rh * 0.28)
        y1 = int(rh * 0.88)
        band = gray_src[y0:y1, :]
        if band.size < 100:
            return []
        for thresh_val in (130, 145, 160, 175):
            _, th = cv2.threshold(band, thresh_val, 255, cv2.THRESH_BINARY)
            th = cv2.morphologyEx(
                th, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (21, 7))
            )
            contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for cnt in contours:
                area = float(cv2.contourArea(cnt))
                if area < 1500:
                    continue
                rect = cv2.minAreaRect(cnt)
                (_cx, _cy), (rw_r, rh_r), _ang = rect
                long_side, short_side = max(rw_r, rh_r), min(rw_r, rh_r)
                if short_side < 14 or long_side < 80:
                    continue
                aspect = long_side / max(short_side, 1.0)
                if aspect < 2.2 or aspect > 8.5:
                    continue
                # Score glyphs on a deskewed warp — AABB padding onto the grille
                # otherwise destroys char-blob counts on oblique bumper plates.
                box = cv2.boxPoints(rect).astype(np.float32)
                box[:, 1] += float(y0)
                w_i, h_i = int(round(long_side)), int(round(short_side))
                if w_i < 60 or h_i < 16:
                    continue
                dst = np.array(
                    [[0, 0], [w_i - 1, 0], [w_i - 1, h_i - 1], [0, h_i - 1]],
                    dtype=np.float32,
                )
                # Order boxPoints consistently (top-left → top-right → bottom-right → bottom-left)
                ordered = self._order_box_points(box)
                try:
                    matrix = cv2.getPerspectiveTransform(ordered, dst)
                    warped = cv2.warpPerspective(roi, matrix, (w_i, h_i))
                except Exception:  # noqa: BLE001
                    continue
                if warped is None or warped.size < 16:
                    continue
                g = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY) if warped.ndim == 3 else warped
                blobs = count_char_like_blobs(g)
                if blobs < 4:
                    continue
                x, y, bw, bh = cv2.boundingRect(ordered.astype(np.int32))
                pad_x, pad_y = max(2, int(bw * 0.03)), max(2, int(bh * 0.05))
                x, y = max(0, x - pad_x), max(0, y - pad_y)
                bw = min(rw - x, bw + 2 * pad_x)
                bh = min(rh - y, bh + 2 * pad_y)
                if bw < 60 or bh < 16:
                    continue
                area_ratio = float(bw * bh) / max(frame_area, 1.0)
                if area_ratio < self.min_area_ratio or area_ratio > self.max_area_ratio:
                    continue
                cy = (y + bh / 2) / max(rh, 1)
                if cy >= 0.90:
                    continue
                aspect_score = 1.0 - abs(aspect - 4.2) / 4.2
                blob_score = min(1.0, blobs / 7.0)
                conf = float(
                    max(
                        0.0,
                        min(
                            0.95,
                            0.42
                            + 0.22 * aspect_score
                            + 0.30 * blob_score
                            + (0.08 if 0.35 <= cy <= 0.85 else 0.0),
                        ),
                    )
                )
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
                        class_name="license_plate",
                    )
                )
        return candidates

    @staticmethod
    def _order_box_points(pts: Any) -> Any:
        """Order 4×2 box points as TL, TR, BR, BL for perspective warp."""
        import numpy as np

        pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
        s = pts.sum(axis=1)
        diff = np.diff(pts, axis=1).reshape(-1)
        tl = pts[np.argmin(s)]
        br = pts[np.argmax(s)]
        tr = pts[np.argmin(diff)]
        bl = pts[np.argmax(diff)]
        return np.array([tl, tr, br, bl], dtype=np.float32)

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


def expand_ind_strip_to_hsrp(
    frame: Any,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    target_aspect: float = 5.2,
    height_scale: float = 2.4,
) -> tuple[Any | None, tuple[int, int, int, int]]:
    """When a crop looks like the HSRP ``IND`` legend, expand right to cover the registration field."""
    if frame is None or not hasattr(frame, "shape"):
        return None, (x1, y1, x2, y2)
    h, w = frame.shape[:2]
    strip_h = max(1, y2 - y1)
    strip_w = max(1, x2 - x1)
    # IND legend is shorter than the registration glyphs — grow vertically.
    plate_h = int(max(strip_h * height_scale, strip_w * 0.55, 28))
    cy = (y1 + y2) // 2
    ny1 = max(0, cy - plate_h // 2)
    ny2 = min(h, cy + plate_h // 2)
    plate_h = max(1, ny2 - ny1)
    nx1 = max(0, x1 - int(strip_w * 0.15))
    nx2 = min(w, nx1 + int(plate_h * target_aspect))
    if nx2 - nx1 < 80 or ny2 - ny1 < 20:
        return None, (x1, y1, x2, y2)
    return frame[ny1:ny2, nx1:nx2].copy(), (nx1, ny1, nx2, ny2)


def _boxes_adjacent(a: PlateDetection, b: PlateDetection) -> bool:
    """True when two plate proposals likely belong to the same physical plate."""
    ax1, ay1 = a.bbox.x, a.bbox.y
    ax2, ay2 = a.bbox.x + a.bbox.w, a.bbox.y + a.bbox.h
    bx1, by1 = b.bbox.x, b.bbox.y
    bx2, by2 = b.bbox.x + b.bbox.w, b.bbox.y + b.bbox.h
    # Positive = separated; negative/zero = overlapping on that axis.
    gap_x = max(ax1, bx1) - min(ax2, bx2)
    gap_y = max(ay1, by1) - min(ay2, by2)
    max_w = max(a.bbox.w, b.bbox.w, 1.0)
    max_h = max(a.bbox.h, b.bbox.h, 1.0)
    return gap_x <= max_w * 0.55 and gap_y <= max_h * 0.55


def merge_adjacent_plate_detections(
    plates: list[PlateDetection],
    *,
    max_merges: int = 6,
) -> list[PlateDetection]:
    """Add union boxes for nearby plate fragments so OCR can see the full plate.

    Original detections are preserved; merged unions are appended with a slightly
    boosted confidence so adaptive ranking prefers the fuller crop when useful.
    """
    if len(plates) < 2:
        return list(plates)
    merged: list[PlateDetection] = list(plates)
    seen: set[tuple[int, int, int, int]] = set()
    for i, a in enumerate(plates):
        for b in plates[i + 1 :]:
            if not _boxes_adjacent(a, b):
                continue
            nx1 = min(a.bbox.x, b.bbox.x)
            ny1 = min(a.bbox.y, b.bbox.y)
            nx2 = max(a.bbox.x + a.bbox.w, b.bbox.x + b.bbox.w)
            ny2 = max(a.bbox.y + a.bbox.h, b.bbox.y + b.bbox.h)
            nw, nh = nx2 - nx1, ny2 - ny1
            if nw < 40 or nh < 12:
                continue
            aspect = nw / max(nh, 1.0)
            if aspect < 1.2 or aspect > 10.0:
                continue
            key = (int(nx1), int(ny1), int(nx2), int(ny2))
            if key in seen:
                continue
            seen.add(key)
            conf = min(0.95, max(a.confidence, b.confidence) + 0.04)
            merged.append(
                PlateDetection(
                    bbox=BoundingBox(float(nx1), float(ny1), float(nw), float(nh), conf),
                    confidence=conf,
                )
            )
            if len(seen) >= max_merges:
                return merged
    return merged


def expand_partial_plate_crop(
    frame: Any,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    grow_left: float = 0.85,
    grow_right: float = 1.15,
    grow_y: float = 0.35,
    grow_up: float | None = None,
) -> tuple[Any | None, tuple[int, int, int, int]]:
    """Widen a partial registration crop so neighboring glyphs are included.

    For likely bottom-line-only motorcycle crops (wide, short, digit-heavy OCR),
    pass a larger ``grow_up`` so the top line (``MH02G``) is recovered.
    """
    if frame is None or not hasattr(frame, "shape"):
        return None, (x1, y1, x2, y2)
    h, w = frame.shape[:2]
    bw, bh = max(1, x2 - x1), max(1, y2 - y1)
    up = grow_y if grow_up is None else grow_up
    nx1 = max(0, int(x1 - bw * grow_left))
    nx2 = min(w, int(x2 + bw * grow_right))
    ny1 = max(0, int(y1 - bh * up))
    ny2 = min(h, int(y2 + bh * grow_y))
    if (nx2 - nx1) <= (x2 - x1) + 4 and (ny2 - ny1) <= (y2 - y1) + 4:
        return None, (x1, y1, x2, y2)
    if nx2 - nx1 < 60 or ny2 - ny1 < 16:
        return None, (x1, y1, x2, y2)
    return frame[ny1:ny2, nx1:nx2].copy(), (nx1, ny1, nx2, ny2)
