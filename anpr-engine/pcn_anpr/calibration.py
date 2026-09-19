"""ANPR camera calibration scoring (installer diagnostic).

Uses an existing ``process_image`` result plus OpenCV image metrics.
Does NOT change YOLO / Hybrid / PaddleOCR detectors.
"""

from __future__ import annotations

import os
from typing import Any

import cv2
import numpy as np

from pcn_anpr.anpr_roi import normalize_roi, roi_to_xyxy, vehicle_in_anpr_roi

# --- Configurable engineering targets (calibration labels, not guarantees) ---
PLATE_WIDTH_GREEN = float(os.getenv("ANPR_CAL_PLATE_W_GREEN", "120"))
PLATE_WIDTH_YELLOW = float(os.getenv("ANPR_CAL_PLATE_W_YELLOW", "80"))
PLATE_AREA_GREEN = float(os.getenv("ANPR_CAL_PLATE_AREA_GREEN", "0.004"))
PLATE_AREA_YELLOW = float(os.getenv("ANPR_CAL_PLATE_AREA_YELLOW", "0.002"))
VEHICLE_AREA_FAR = float(os.getenv("ANPR_CAL_VEHICLE_AREA_FAR", "0.04"))
OCR_CONF_GREEN = float(os.getenv("ANPR_CAL_OCR_CONF_GREEN", "0.75"))
OCR_CONF_YELLOW = float(os.getenv("ANPR_CAL_OCR_CONF_YELLOW", "0.45"))
DET_CONF_GREEN = float(os.getenv("ANPR_CAL_DET_CONF_GREEN", "0.55"))
BRIGHT_LOW = float(os.getenv("ANPR_CAL_BRIGHT_LOW", "45"))
BRIGHT_HIGH = float(os.getenv("ANPR_CAL_BRIGHT_HIGH", "210"))
SHARP_GREEN = float(os.getenv("ANPR_CAL_SHARP_GREEN", "80"))
SHARP_YELLOW = float(os.getenv("ANPR_CAL_SHARP_YELLOW", "35"))
GLARE_FRAC_WARN = float(os.getenv("ANPR_CAL_GLARE_FRAC", "0.12"))
ASPECT_STEEP_LOW = float(os.getenv("ANPR_CAL_ASPECT_LOW", "1.6"))
ASPECT_STEEP_HIGH = float(os.getenv("ANPR_CAL_ASPECT_HIGH", "7.5"))


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _bbox_wh(bbox: list[float] | None) -> tuple[float, float]:
    if not bbox or len(bbox) < 4:
        return 0.0, 0.0
    x1, y1, x2, y2 = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    return max(0.0, x2 - x1), max(0.0, y2 - y1)


def _crop_bgr(frame: Any, bbox: list[float] | None) -> Any | None:
    if frame is None or not bbox or len(bbox) < 4:
        return None
    fh, fw = int(frame.shape[0]), int(frame.shape[1])
    x1 = max(0, min(fw - 1, int(bbox[0])))
    y1 = max(0, min(fh - 1, int(bbox[1])))
    x2 = max(0, min(fw, int(bbox[2])))
    y2 = max(0, min(fh, int(bbox[3])))
    if x2 <= x1 or y2 <= y1:
        return None
    return frame[y1:y2, x1:x2]


def image_quality_metrics(bgr: Any) -> dict[str, float]:
    """Brightness / sharpness / glare on a BGR crop or frame."""
    if bgr is None or not hasattr(bgr, "size") or bgr.size == 0:
        return {"brightness": 0.0, "sharpness": 0.0, "glare_fraction": 0.0}
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if len(bgr.shape) == 3 else bgr
    brightness = float(np.mean(gray))
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    glare = float(np.mean(gray >= 245))
    return {
        "brightness": round(brightness, 2),
        "sharpness": round(sharp, 2),
        "glare_fraction": round(glare, 4),
    }


def _band_score(value: float, green: float, yellow: float, *, higher_is_better: bool = True) -> float:
    if higher_is_better:
        if value >= green:
            return 1.0
        if value >= yellow:
            return 0.55
        return max(0.0, value / max(yellow, 1e-6) * 0.4)
    # lower band unused for now
    return _clamp01(1.0 - value)


def _status_from_scores(overall: float, hard_red: bool) -> str:
    if hard_red:
        return "RED"
    if overall >= 0.75:
        return "GREEN"
    if overall >= 0.45:
        return "YELLOW"
    return "RED"


def build_calibration_report(
    result: dict[str, Any],
    *,
    frame_bgr: Any = None,
    anpr_roi: dict[str, Any] | None = None,
    camera_id: str | None = None,
) -> dict[str, Any]:
    """Build installer calibration report from an ANPR ``process_image`` result."""
    timing = result.get("timing") or {}
    vehicles = list(result.get("vehicles") or [])
    detections = list(result.get("detections") or [])
    vehicle_results = list(result.get("vehicle_results") or [])
    plates = list(result.get("plates") or [])
    candidates = list(timing.get("plate_candidates") or [])

    shape = result.get("decoded_shape") or []
    fh = int(shape[0]) if len(shape) >= 2 else (int(frame_bgr.shape[0]) if frame_bgr is not None else 0)
    fw = int(shape[1]) if len(shape) >= 2 else (int(frame_bgr.shape[1]) if frame_bgr is not None else 0)
    frame_area = float(max(1, fh * fw))

    # Prefer primary detection / primary vehicle plate.
    primary_det = next((d for d in detections if d.get("is_primary")), None)
    if primary_det is None and detections:
        primary_det = detections[0]
    primary_veh = next((v for v in vehicles if v.get("is_primary")), None)
    if primary_veh is None and vehicles:
        primary_veh = vehicles[0]
    primary_vr = next((v for v in vehicle_results if v.get("is_primary")), None)
    if primary_vr is None and vehicle_results:
        primary_vr = vehicle_results[0]

    plate_bbox = list((primary_det or {}).get("plate_bbox") or (primary_det or {}).get("bbox") or [])
    if not plate_bbox and primary_vr:
        plate_bbox = list(primary_vr.get("plate_bbox") or [])
    if not plate_bbox:
        for p in plates:
            if p.get("bbox"):
                plate_bbox = list(p.get("bbox") or [])
                if p.get("matches_pattern"):
                    break
        if not plate_bbox and candidates:
            plate_bbox = list(candidates[0].get("bbox") or [])

    vehicle_bbox = list(
        (primary_det or {}).get("vehicle_bbox")
        or (primary_vr or {}).get("vehicle_bbox")
        or (primary_veh or {}).get("bbox")
        or []
    )
    plate_w, plate_h = _bbox_wh(plate_bbox)
    plate_area_ratio = (plate_w * plate_h) / frame_area if plate_w and plate_h else 0.0
    plate_aspect = (plate_w / plate_h) if plate_h > 1e-6 else 0.0

    vehicle_conf = float(
        (primary_det or {}).get("confidence")
        or (primary_veh or {}).get("confidence")
        or (primary_vr or {}).get("confidence")
        or 0.0
    )
    plate_conf = float(
        (primary_det or {}).get("plate_confidence")
        or (primary_vr or {}).get("plate_confidence")
        or (primary_det or {}).get("confidence")
        or 0.0
    )
    ocr_conf = float(
        (primary_det or {}).get("ocr_confidence")
        or (primary_vr or {}).get("ocr_confidence")
        or 0.0
    )
    plate_text = str(
        (primary_det or {}).get("plate")
        or (primary_vr or {}).get("best_plate")
        or (primary_vr or {}).get("plate")
        or ""
    )
    matches_pattern = bool(
        (primary_det or {}).get("matches_indian_pattern")
        or (primary_vr or {}).get("matches_pattern")
    )
    if not plate_text:
        for p in plates:
            if p.get("matches_pattern"):
                plate_text = str(p.get("normalized_text") or p.get("normalized_plate") or "")
                ocr_conf = float(p.get("ocr_confidence") or ocr_conf)
                plate_conf = float(p.get("plate_confidence") or plate_conf)
                matches_pattern = True
                break

    vehicle_area_ratio = float((primary_veh or {}).get("area_ratio") or 0.0)
    if vehicle_area_ratio <= 0 and vehicle_bbox and len(vehicle_bbox) >= 4:
        vw, vh = _bbox_wh(vehicle_bbox)
        vehicle_area_ratio = (vw * vh) / frame_area

    # Quality reasons from first matching candidate
    quality_reasons: list[str] = []
    quality_score = 0.0
    for c in candidates:
        q = c.get("quality") or {}
        if q:
            quality_reasons = list(q.get("reasons") or [])
            quality_score = float(q.get("score") or 0.0)
            if c.get("bbox") == plate_bbox or not plate_bbox:
                break

    plate_crop = _crop_bgr(frame_bgr, plate_bbox) if plate_bbox else None
    frame_metrics = image_quality_metrics(frame_bgr)
    plate_metrics = image_quality_metrics(plate_crop) if plate_crop is not None else frame_metrics
    brightness = plate_metrics["brightness"] if plate_crop is not None else frame_metrics["brightness"]
    sharpness = plate_metrics["sharpness"] if plate_crop is not None else frame_metrics["sharpness"]
    glare_frac = plate_metrics["glare_fraction"] if plate_crop is not None else frame_metrics["glare_fraction"]

    roi_norm = normalize_roi(anpr_roi) or normalize_roi(timing.get("roi_norm"))
    roi_enabled = bool(roi_norm) or bool(timing.get("roi_enabled"))
    vehicle_in_roi = None
    plate_in_roi = None
    if roi_norm and fh and fw:
        rxy = roi_to_xyxy(roi_norm, (fh, fw))
        if vehicle_bbox and len(vehicle_bbox) >= 4:
            vehicle_in_roi = vehicle_in_anpr_roi(vehicle_bbox, rxy)
        if plate_bbox and len(plate_bbox) >= 4:
            pcx = 0.5 * (float(plate_bbox[0]) + float(plate_bbox[2]))
            pcy = 0.5 * (float(plate_bbox[1]) + float(plate_bbox[3]))
            plate_in_roi = rxy[0] <= pcx <= rxy[2] and rxy[1] <= pcy <= rxy[3]

    reasons: list[str] = []
    guidance: list[str] = []
    hard_red = False

    has_plate = plate_w >= 1 and plate_h >= 1
    has_vehicle = bool(vehicles)

    if not has_vehicle:
        reasons.append("No vehicle detected")
        guidance.append("Place a vehicle at the intended gate crossing and recapture")
        hard_red = True
    elif not has_plate:
        reasons.append("No plate detected")
        guidance.append("Aim the camera so the plate faces the lens and is unobstructed")
        hard_red = True
    else:
        if plate_w < PLATE_WIDTH_YELLOW:
            reasons.append("Plate too small")
            guidance.append("Move the camera closer or zoom in so the plate is larger in frame")
            hard_red = True
        elif plate_w < PLATE_WIDTH_GREEN:
            reasons.append("Plate size below calibration target")
            guidance.append("Move slightly closer or use a longer focal length")

        if vehicle_area_ratio > 0 and vehicle_area_ratio < VEHICLE_AREA_FAR and plate_w < PLATE_WIDTH_GREEN:
            reasons.append("Vehicle too far")
            guidance.append("Mount closer to the lane or wait for the vehicle nearer the gate line")

        if plate_aspect and (plate_aspect < ASPECT_STEEP_LOW or plate_aspect > ASPECT_STEEP_HIGH):
            reasons.append("Plate angle looks steep")
            guidance.append("Aim more toward the lane so the plate faces the camera more squarely")

        if brightness < BRIGHT_LOW:
            reasons.append("Low light")
            guidance.append("Improve illumination or reduce shutter/gain limits if controllable")
        elif brightness > BRIGHT_HIGH:
            reasons.append("Frame very bright")
            guidance.append("Reduce exposure or avoid pointing into strong light")

        if sharpness < SHARP_YELLOW:
            reasons.append("Soft / blurry plate")
            guidance.append("Check focus and mounting vibration; avoid motion blur")
            hard_red = hard_red or sharpness < SHARP_YELLOW * 0.5

        if glare_frac >= GLARE_FRAC_WARN or any(
            "bright_lamp" in str(r) or "illumination_dominated" in str(r) for r in quality_reasons
        ):
            reasons.append("Strong glare")
            guidance.append("Reduce direct headlight / sun glare into the camera")

        if any("few_char" in str(r) or "occlusion" in str(r).lower() for r in quality_reasons):
            reasons.append("Plate partially hidden or hard to read")
            guidance.append("Ensure the full plate is visible at the crossing point")

        if has_plate and not matches_pattern:
            reasons.append("OCR confidence too low" if ocr_conf < OCR_CONF_YELLOW else "OCR did not produce a valid Indian plate")
            if ocr_conf < OCR_CONF_YELLOW:
                hard_red = True
            guidance.append("Improve plate size, focus, and lighting before relying on OCR")

        if roi_enabled and vehicle_in_roi is False:
            reasons.append("Vehicle outside ANPR Zone")
            guidance.append("Reposition the vehicle into the gate ROI or redraw the ANPR Zone")
        if roi_enabled and plate_in_roi is False and plate_bbox:
            reasons.append("Plate outside / near edge of ANPR Zone")
            guidance.append("Expand the ANPR Zone slightly or aim so the plate sits inside the zone")

    # Component scores 0..1
    size_score = 0.0
    if has_plate:
        size_score = _band_score(plate_w, PLATE_WIDTH_GREEN, PLATE_WIDTH_YELLOW)
        size_score = 0.6 * size_score + 0.4 * _band_score(
            plate_area_ratio, PLATE_AREA_GREEN, PLATE_AREA_YELLOW
        )

    visibility_score = 0.0
    if has_plate:
        visibility_score = 0.7
        if quality_score > 0:
            visibility_score = 0.4 + 0.6 * _clamp01(quality_score)
        if any("few_char" in str(r) for r in quality_reasons):
            visibility_score *= 0.6

    image_score = 0.5
    if has_plate or has_vehicle:
        b_ok = BRIGHT_LOW <= brightness <= BRIGHT_HIGH
        image_score = (0.5 if b_ok else 0.25) + 0.5 * _band_score(sharpness, SHARP_GREEN, SHARP_YELLOW)
        if glare_frac >= GLARE_FRAC_WARN:
            image_score *= 0.7

    det_score = _band_score(max(vehicle_conf, plate_conf), DET_CONF_GREEN, 0.35) if has_vehicle else 0.0
    ocr_score = 0.0
    if matches_pattern and ocr_conf > 0:
        ocr_score = _band_score(ocr_conf, OCR_CONF_GREEN, OCR_CONF_YELLOW)
    elif has_plate and ocr_conf > 0:
        ocr_score = 0.35 * _band_score(ocr_conf, OCR_CONF_GREEN, OCR_CONF_YELLOW)

    overall = (
        0.25 * size_score
        + 0.20 * visibility_score
        + 0.20 * image_score
        + 0.15 * det_score
        + 0.20 * ocr_score
    )
    if not has_plate:
        overall = min(overall, 0.25)
    status = _status_from_scores(overall, hard_red)

    # Deduplicate guidance preserving order
    seen: set[str] = set()
    tips: list[str] = []
    for g in guidance:
        if g not in seen:
            seen.add(g)
            tips.append(g)

    targets = {
        "plate_width_green_px": PLATE_WIDTH_GREEN,
        "plate_width_yellow_px": PLATE_WIDTH_YELLOW,
        "plate_area_green": PLATE_AREA_GREEN,
        "note": "Engineering calibration targets — not a guaranteed accuracy claim",
    }

    report = {
        "camera_id": camera_id,
        "status": status,
        "overall_score": round(overall, 3),
        "component_scores": {
            "plate_size": round(size_score, 3),
            "plate_visibility": round(visibility_score, 3),
            "image_quality": round(image_score, 3),
            "detection_confidence": round(det_score, 3),
            "ocr_quality": round(ocr_score, 3),
        },
        "reasons": reasons,
        "guidance": tips,
        "metrics": {
            "frame_wh": [fw, fh],
            "vehicle_count": len(vehicles),
            "plate_count": len(detections) or (1 if has_plate else 0),
            "vehicle_bbox": vehicle_bbox,
            "plate_bbox": plate_bbox,
            "plate_width_px": round(plate_w, 1),
            "plate_height_px": round(plate_h, 1),
            "plate_area_ratio": round(plate_area_ratio, 5),
            "plate_aspect": round(plate_aspect, 3),
            "vehicle_area_ratio": round(vehicle_area_ratio, 4),
            "vehicle_confidence": round(vehicle_conf, 4),
            "plate_confidence": round(plate_conf, 4),
            "ocr_confidence": round(ocr_conf, 4),
            "plate_text": plate_text,
            "matches_indian_pattern": matches_pattern,
            "brightness": brightness,
            "sharpness": sharpness,
            "glare_fraction": glare_frac,
            "quality_score": round(quality_score, 3),
            "quality_reasons": quality_reasons,
            "roi_enabled": roi_enabled,
            "roi_norm": roi_norm,
            "vehicle_in_roi": vehicle_in_roi,
            "plate_in_roi": plate_in_roi,
            "processing_ms": int(result.get("processing_ms") or 0),
        },
        "targets": targets,
        "overlays": {
            "vehicle_bbox": vehicle_bbox,
            "plate_bbox": plate_bbox,
            "roi": roi_norm,
        },
    }
    return report


def calibration_summary_for_storage(report: dict[str, Any]) -> dict[str, Any]:
    """Compact summary persisted on the camera (no image bytes)."""
    m = report.get("metrics") or {}
    return {
        "status": report.get("status"),
        "overall_score": report.get("overall_score"),
        "component_scores": report.get("component_scores"),
        "reasons": report.get("reasons") or [],
        "guidance": report.get("guidance") or [],
        "plate_width_px": m.get("plate_width_px"),
        "plate_height_px": m.get("plate_height_px"),
        "plate_text": m.get("plate_text"),
        "ocr_confidence": m.get("ocr_confidence"),
        "brightness": m.get("brightness"),
        "sharpness": m.get("sharpness"),
        "roi_enabled": m.get("roi_enabled"),
        "vehicle_in_roi": m.get("vehicle_in_roi"),
        "processing_ms": m.get("processing_ms"),
    }
