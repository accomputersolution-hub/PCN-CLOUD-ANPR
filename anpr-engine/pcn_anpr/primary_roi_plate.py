from __future__ import annotations

"""Second-stage plate search inside the primary-vehicle / motorcycle ROI.

Used when the first full-frame pass finds no reliable Indian plate (e.g. night
scenes where the OpenCV vehicle box misses the bike, or two-line plates are
filtered by single-line aspect priors).

Does NOT weaken taillight rejection — only proposes crops that pass
``assess_plate_crop`` (lights still rejected; neighboring white plates kept).
"""

from typing import Any

from pcn_anpr.interfaces import BoundingBox, PlateDetection, VehicleDetection
from pcn_anpr.plate_quality import assess_plate_crop
from pcn_anpr.vehicle_assoc import VehicleRef


def _clip(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def build_primary_search_rois(
    frame: Any,
    *,
    vehicles: list[VehicleDetection],
    vehicle_refs: list[VehicleRef],
    primary_idx: int | None,
) -> list[tuple[str, Any, int, int]]:
    """Return (label, roi_bgr, origin_x, origin_y) bands to search for plates.

    Includes center/lower motorcycle bands plus the primary vehicle bbox.
    Background vehicles are intentionally omitted here — use
    ``build_vehicle_search_rois`` for per-vehicle searches.
    """
    if frame is None or not hasattr(frame, "shape"):
        return []
    fh, fw = int(frame.shape[0]), int(frame.shape[1])
    rois: list[tuple[str, Any, int, int]] = []

    def _add(label: str, x1: int, y1: int, x2: int, y2: int) -> None:
        x1, y1 = _clip(x1, 0, fw - 1), _clip(y1, 0, fh - 1)
        x2, y2 = _clip(x2, x1 + 8, fw), _clip(y2, y1 + 8, fh)
        if (x2 - x1) < 60 or (y2 - y1) < 40:
            return
        rois.append((label, frame[y1:y2, x1:x2].copy(), x1, y1))

    # Center / lower motorcycle band — Manual ANPR night bike rears.
    _add("center_lower", int(fw * 0.18), int(fh * 0.32), int(fw * 0.82), int(fh * 0.95))
    _add("center_mid", int(fw * 0.22), int(fh * 0.40), int(fw * 0.78), int(fh * 0.88))

    if primary_idx is not None and primary_idx < len(vehicle_refs):
        vx1, vy1, vx2, vy2 = [int(v) for v in vehicle_refs[primary_idx].bbox_xyxy]
        pad_x = int((vx2 - vx1) * 0.08)
        pad_y = int((vy2 - vy1) * 0.08)
        _add("primary_full", vx1 - pad_x, vy1 - pad_y, vx2 + pad_x, vy2 + pad_y)
        mid_y = vy1 + int((vy2 - vy1) * 0.42)
        _add("primary_lower", vx1 - pad_x, mid_y, vx2 + pad_x, vy2 + pad_y)

    return rois


def build_vehicle_search_rois(
    frame: Any,
    *,
    vehicle_refs: list[VehicleRef],
    vehicle_index: int,
) -> list[tuple[str, Any, int, int]]:
    """Plate-search bands inside one vehicle bbox (independent of other vehicles)."""
    if frame is None or not hasattr(frame, "shape"):
        return []
    if vehicle_index < 0 or vehicle_index >= len(vehicle_refs):
        return []
    fh, fw = int(frame.shape[0]), int(frame.shape[1])
    ref = vehicle_refs[vehicle_index]
    vx1, vy1, vx2, vy2 = [int(v) for v in ref.bbox_xyxy]
    pad_x = max(8, int((vx2 - vx1) * 0.10))
    pad_y = max(8, int((vy2 - vy1) * 0.10))
    tid = ref.track_id or f"i{vehicle_index}"
    rois: list[tuple[str, Any, int, int]] = []

    def _add(label: str, x1: int, y1: int, x2: int, y2: int) -> None:
        x1, y1 = _clip(x1, 0, fw - 1), _clip(y1, 0, fh - 1)
        x2, y2 = _clip(x2, x1 + 8, fw), _clip(y2, y1 + 8, fh)
        if (x2 - x1) < 40 or (y2 - y1) < 30:
            return
        rois.append((label, frame[y1:y2, x1:x2].copy(), x1, y1))

    _add(f"veh_{tid}_full", vx1 - pad_x, vy1 - pad_y, vx2 + pad_x, vy2 + pad_y)
    mid_y = vy1 + int((vy2 - vy1) * 0.40)
    _add(f"veh_{tid}_lower", vx1 - pad_x, mid_y, vx2 + pad_x, vy2 + pad_y)
    return rois


def _roi_variants(roi: Any) -> list[tuple[str, Any]]:
    """Image variants for night / low-contrast motorcycle plates."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return [("original", roi)]

    out: list[tuple[str, Any]] = [("original", roi)]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)
    out.append(("clahe", cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR)))
    out.append(("gray", cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)))
    sharp = cv2.addWeighted(clahe, 1.45, cv2.GaussianBlur(clahe, (0, 0), 1.2), -0.45, 0)
    out.append(("sharpen_clahe", cv2.cvtColor(sharp, cv2.COLOR_GRAY2BGR)))
    contrast = cv2.convertScaleAbs(clahe, alpha=1.35, beta=8)
    out.append(("contrast", cv2.cvtColor(contrast, cv2.COLOR_GRAY2BGR)))
    for scale, name in ((2.0, "x2"), (3.0, "x3")):
        up = cv2.resize(roi, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        out.append((f"upscale_{name}", up))
        g = cv2.cvtColor(up, cv2.COLOR_BGR2GRAY)
        c = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(g)
        out.append((f"upscale_{name}_clahe", cv2.cvtColor(c, cv2.COLOR_GRAY2BGR)))
    return out


def detect_plates_in_rois(
    frame: Any,
    rois: list[tuple[str, Any, int, int]],
    *,
    max_proposals: int = 10,
    class_prefix: str = "primary_roi",
    diagnostics: dict[str, Any] | None = None,
) -> list[PlateDetection]:
    """Propose plate boxes inside the given ROI bands (taillight rejection intact)."""
    from pcn_anpr.opencv_plate import OpenCVPlateDetector

    if diagnostics is not None:
        diagnostics["roi_labels"] = [label for label, *_ in rois]
        diagnostics["roi_count"] = len(rois)
        diagnostics["roi_shapes"] = [
            {"label": label, "h": int(roi.shape[0]), "w": int(roi.shape[1]), "ox": ox, "oy": oy}
            for label, roi, ox, oy in rois
        ]
    if not rois:
        if diagnostics is not None:
            diagnostics["variant_count"] = 0
            diagnostics["proposals_before_cap"] = 0
            diagnostics["twoline_candidates"] = 0
        return []

    detector = OpenCVPlateDetector(
        min_aspect=1.25,
        max_aspect=6.5,
        min_area_ratio=0.0006,
        max_area_ratio=0.35,
        min_confidence=0.12,
        max_candidates=8,
    )

    fh = int(frame.shape[0])
    fw = int(frame.shape[1])
    seen: set[tuple[int, int, int, int]] = set()
    proposals: list[PlateDetection] = []

    for label, roi, ox, oy in rois:
        scale_back = 1.0
        for vname, variant in _roi_variants(roi):
            if variant.shape[0] != roi.shape[0] or variant.shape[1] != roi.shape[1]:
                scale_back = roi.shape[1] / max(variant.shape[1], 1)
            else:
                scale_back = 1.0
            dets = detector.detect(variant, None) or []
            for p in dets:
                rx = float(p.bbox.x) * scale_back
                ry = float(p.bbox.y) * scale_back
                rw = float(p.bbox.w) * scale_back
                rh = float(p.bbox.h) * scale_back
                x1 = int(ox + rx)
                y1 = int(oy + ry)
                x2 = int(ox + rx + rw)
                y2 = int(oy + ry + rh)
                x1, y1 = _clip(x1, 0, fw - 1), _clip(y1, 0, fh - 1)
                x2, y2 = _clip(x2, x1 + 4, fw), _clip(y2, y1 + 4, fh)
                key = (x1 // 4 * 4, y1 // 4 * 4, x2 // 4 * 4, y2 // 4 * 4)
                if key in seen:
                    continue
                crop = frame[y1:y2, x1:x2]
                assessment = assess_plate_crop(
                    crop,
                    bbox_xyxy=(float(x1), float(y1), float(x2), float(y2)),
                    frame_shape=(fh, fw),
                )
                if assessment.reject:
                    continue
                asp = (x2 - x1) / max(y2 - y1, 1)
                twoline_bonus = 0.08 if 1.4 <= asp <= 3.2 else 0.0
                cy = (y1 + y2) * 0.5 / max(fh, 1)
                rear_bonus = 0.05 if 0.45 <= cy <= 0.90 else 0.0
                cx = (x1 + x2) * 0.5 / max(fw, 1)
                if cy < 0.40 and cx < 0.35:
                    rear_bonus -= 0.12
                conf = float(
                    min(
                        0.95,
                        float(p.confidence) * 0.55
                        + assessment.score * 0.40
                        + twoline_bonus
                        + rear_bonus,
                    )
                )
                if conf < 0.18:
                    continue
                seen.add(key)
                proposals.append(
                    PlateDetection(
                        bbox=BoundingBox(float(x1), float(y1), float(x2 - x1), float(y2 - y1), conf),
                        confidence=conf,
                        class_name=f"{class_prefix}:{label}:{vname}",
                    )
                )

    twoline_n = 0
    for p in proposals:
        asp = float(p.bbox.w) / max(float(p.bbox.h), 1.0)
        if 1.3 <= asp <= 3.5:
            twoline_n += 1
    if diagnostics is not None:
        sample_variants = _roi_variants(rois[0][1]) if rois else []
        diagnostics["variant_count"] = len(sample_variants)
        diagnostics["variant_names"] = [n for n, _ in sample_variants]
        diagnostics["proposals_before_cap"] = len(proposals)
        diagnostics["twoline_candidates"] = twoline_n

    proposals.sort(key=lambda p: (p.confidence, p.bbox.w * p.bbox.h), reverse=True)
    return proposals[:max_proposals]


def detect_plates_in_primary_rois(
    frame: Any,
    *,
    vehicles: list[VehicleDetection],
    vehicle_refs: list[VehicleRef],
    primary_idx: int | None,
    max_proposals: int = 10,
    diagnostics: dict[str, Any] | None = None,
) -> list[PlateDetection]:
    """Propose two-line-friendly plate boxes inside primary / center-lower ROIs."""
    rois = build_primary_search_rois(
        frame, vehicles=vehicles, vehicle_refs=vehicle_refs, primary_idx=primary_idx
    )
    return detect_plates_in_rois(
        frame,
        rois,
        max_proposals=max_proposals,
        class_prefix="primary_roi",
        diagnostics=diagnostics,
    )


def detect_plates_in_vehicle_rois(
    frame: Any,
    *,
    vehicle_refs: list[VehicleRef],
    vehicle_index: int,
    max_proposals: int = 6,
    diagnostics: dict[str, Any] | None = None,
) -> list[PlateDetection]:
    """Plate proposals strictly inside one vehicle ROI (never promoted to primary)."""
    rois = build_vehicle_search_rois(
        frame, vehicle_refs=vehicle_refs, vehicle_index=vehicle_index
    )
    tid = (
        vehicle_refs[vehicle_index].track_id
        if 0 <= vehicle_index < len(vehicle_refs)
        else f"i{vehicle_index}"
    )
    return detect_plates_in_rois(
        frame,
        rois,
        max_proposals=max_proposals,
        class_prefix=f"vehicle_roi:{tid}",
        diagnostics=diagnostics,
    )
