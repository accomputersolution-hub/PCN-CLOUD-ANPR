from __future__ import annotations

"""Tighten loose vehicle-front crops before OCR.

Wide bumper/grille crops cause PaddleOCR to read chrome slots as plate text
(e.g. ``CCCQODD P7F9543`` → false ``DP7F9543``) even when the real plate is
visible lower in the same crop. Prefer the lower bumper band for OCR.
"""

from typing import Any

from pcn_anpr.plate_quality import assess_plate_crop


def is_loose_vehicle_front_crop(crop: Any) -> bool:
    """True when crop looks like a vehicle front / bumper scene, not a tight plate."""
    if crop is None or not hasattr(crop, "shape") or len(crop.shape) < 2:
        return False
    h, w = int(crop.shape[0]), int(crop.shape[1])
    if h < 80 or w < 120:
        return False
    aspect = w / max(h, 1)
    # Portrait motorcycle / bike-rear scenes are not Scorpio-style grille fronts.
    # Lower-band refine would slice two-line plates (MH02G / D7249) in half.
    if aspect < 1.15:
        return False
    # Already a compact two-line plate crop (squarish white plate, not bumper scene).
    if 1.15 <= aspect <= 2.8 and h <= 220 and w <= 420:
        return False
    # Tight plates are usually wide (aspect >= ~3). Tall/square front views need refine.
    if aspect <= 3.2 and h >= 90:
        return True
    if h >= 160 and aspect <= 4.0:
        return True
    return False


def refine_loose_plate_crop(
    crop: Any,
    *,
    min_confidence: float = 0.2,
    plate_detector: Any | None = None,
) -> tuple[Any | None, tuple[int, int, int, int] | None, dict[str, Any]]:
    """Return a tighter plate subcrop when the input is a loose vehicle-front view.

    Indian front plates sit on the bumper (lower half). Searching the full crop
    first often locks onto the grille chrome and must be avoided.

    Uses the configured ``plate_detector`` when provided (YOLO or OpenCV). Does
    not secretly construct OpenCV while YOLO plate mode is active.
    """
    meta: dict[str, Any] = {"refined": False, "reason": "not_loose"}
    if not is_loose_vehicle_front_crop(crop):
        return None, None, meta

    try:
        import cv2  # noqa: F401
    except ImportError:
        return None, None, {**meta, "reason": "no_opencv"}

    h, w = int(crop.shape[0]), int(crop.shape[1])
    detector = plate_detector
    if detector is None:
        from pcn_anpr.opencv_plate import OpenCVPlateDetector

        detector = OpenCVPlateDetector(min_confidence=min_confidence, max_candidates=8)
    meta["plate_detector"] = type(detector).__name__

    # Only search lower bands — grille lives in the upper half of these crops.
    searches: list[tuple[str, float]] = [
        ("lower_48", 0.48),
        ("lower_55", 0.55),
        ("lower_40", 0.40),
    ]

    best: tuple[float, Any, tuple[int, int, int, int], str] | None = None
    for label, frac in searches:
        y0 = int(h * frac)
        region = crop[y0:, :]
        if region is None or getattr(region, "size", 0) < 64:
            continue
        dets = detector.detect(region, None) or []
        for p in dets:
            x1 = int(p.bbox.x)
            y1 = int(p.bbox.y) + y0
            x2 = x1 + int(p.bbox.w)
            y2 = y1 + int(p.bbox.h)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 - x1 < 70 or y2 - y1 < 14:
                continue
            aspect = (x2 - x1) / max(y2 - y1, 1)
            if aspect < 2.4:
                continue
            # Reject detections that still sit too high in the parent crop.
            cy = (y1 + y2) * 0.5 / max(h, 1)
            if cy < 0.45:
                continue
            sub = crop[y1:y2, x1:x2]
            assessment = assess_plate_crop(
                sub,
                bbox_xyxy=(float(x1), float(y1), float(x2), float(y2)),
                frame_shape=(h, w),
            )
            if assessment.reject or assessment.char_blobs < 4:
                continue
            score = (
                float(p.confidence) * 0.30
                + assessment.score * 0.45
                + min(1.0, aspect / 4.5) * 0.15
                + min(1.0, cy) * 0.10
            )
            if best is None or score > best[0]:
                best = (score, sub.copy(), (x1, y1, x2, y2), label)

    if best is not None:
        _score, sub, xyxy, label = best
        return (
            sub,
            xyxy,
            {
                "refined": True,
                "reason": f"inner_detect:{label}",
                "score": round(_score, 4),
                "xyxy": list(xyxy),
            },
        )

    # Fallback: whole lower bumper band (empirically recovers UP78… from Scorpio fronts).
    y0 = int(h * 0.52)
    x0, x1 = int(w * 0.05), int(w * 0.95)
    band = crop[y0:, x0:x1].copy()
    if band.size >= 64 and band.shape[0] >= 18:
        assessment = assess_plate_crop(band)
        if assessment.char_blobs >= 3:
            return (
                band,
                (x0, y0, x1, h),
                {
                    "refined": True,
                    "reason": "lower_band_fallback",
                    "score": assessment.score,
                    "char_blobs": assessment.char_blobs,
                },
            )

    return None, None, {**meta, "reason": "no_inner_plate"}


def offset_bbox(
    parent_xyxy: tuple[int, int, int, int] | list[int],
    inner_xyxy: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """Map a crop-local box back into parent-frame coordinates."""
    px1, py1, _px2, _py2 = [int(v) for v in parent_xyxy]
    ix1, iy1, ix2, iy2 = inner_xyxy
    return (px1 + ix1, py1 + iy1, px1 + ix2, py1 + iy2)
