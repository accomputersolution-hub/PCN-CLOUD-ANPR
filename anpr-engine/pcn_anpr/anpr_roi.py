"""Camera ANPR zone (normalized rectangle) helpers.

Used to filter YOLO vehicles before plate/OCR. Does not change detectors.
"""

from __future__ import annotations

from typing import Any, Sequence

# Keep vehicle if bottom-center is in ROI, or bbox overlaps ROI enough
# that the front/plate region can sit in the gate zone.
DEFAULT_OVERLAP_MIN = 0.05


def normalize_roi(roi: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a validated enabled ROI dict, or None if disabled/absent."""
    if not roi or not isinstance(roi, dict):
        return None
    if not bool(roi.get("enabled", True)):
        return None
    try:
        x = float(roi["x"])
        y = float(roi["y"])
        w = float(roi["w"])
        h = float(roi["h"])
    except (KeyError, TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    x = max(0.0, min(1.0, x))
    y = max(0.0, min(1.0, y))
    w = max(1e-6, min(1.0 - x, w))
    h = max(1e-6, min(1.0 - y, h))
    return {"enabled": True, "x": x, "y": y, "w": w, "h": h}


def roi_to_xyxy(
    roi: dict[str, Any],
    frame_shape: tuple[int, int],
) -> tuple[float, float, float, float]:
    """Normalized ROI → pixel xyxy. ``frame_shape`` is (h, w)."""
    fh, fw = int(frame_shape[0]), int(frame_shape[1])
    x1 = float(roi["x"]) * fw
    y1 = float(roi["y"]) * fh
    x2 = x1 + float(roi["w"]) * fw
    y2 = y1 + float(roi["h"]) * fh
    return x1, y1, x2, y2


def _point_in_xyxy(px: float, py: float, box: tuple[float, float, float, float]) -> bool:
    x1, y1, x2, y2 = box
    return x1 <= px <= x2 and y1 <= py <= y2


def _iou_vs_vehicle(
    vbox: tuple[float, float, float, float],
    rbox: tuple[float, float, float, float],
) -> float:
    """Intersection over vehicle area (not union)."""
    ax1, ay1, ax2, ay2 = vbox
    bx1, by1, bx2, by2 = rbox
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    v_area = max(1.0, (ax2 - ax1) * (ay2 - ay1))
    return inter / v_area


def vehicle_in_anpr_roi(
    bbox_xyxy: Sequence[float],
    roi_xyxy: tuple[float, float, float, float],
    *,
    overlap_min: float = DEFAULT_OVERLAP_MIN,
) -> bool:
    """True if vehicle belongs in the gate ANPR zone.

    Prefer bottom-center (ground contact). Also keep if vehicle bbox overlaps
    the ROI enough that a protruding body still has front/plate in zone.
    """
    if len(bbox_xyxy) < 4:
        return False
    x1, y1, x2, y2 = (float(bbox_xyxy[0]), float(bbox_xyxy[1]), float(bbox_xyxy[2]), float(bbox_xyxy[3]))
    cx = 0.5 * (x1 + x2)
    bottom_cy = y2
    if _point_in_xyxy(cx, bottom_cy, roi_xyxy):
        return True
    return _iou_vs_vehicle((x1, y1, x2, y2), roi_xyxy) >= float(overlap_min)


def filter_vehicles_by_anpr_roi(
    vehicles: list[Any],
    *,
    anpr_roi: dict[str, Any] | None,
    frame_shape: tuple[int, int],
    overlap_min: float = DEFAULT_OVERLAP_MIN,
) -> tuple[list[Any], dict[str, Any]]:
    """Filter detector vehicles by optional camera ANPR ROI.

    Returns (kept_vehicles, stats). If ROI disabled, returns vehicles unchanged.
    """
    roi = normalize_roi(anpr_roi)
    total = len(vehicles)
    stats: dict[str, Any] = {
        "roi_enabled": bool(roi),
        "roi_norm": roi,
        "roi_bbox_xyxy": None,
        "total_yolo_vehicles": total,
        "roi_vehicles": total,
        "ignored_outside_roi": 0,
    }
    if not roi:
        return list(vehicles), stats

    roi_xyxy = roi_to_xyxy(roi, frame_shape)
    stats["roi_bbox_xyxy"] = [round(v, 1) for v in roi_xyxy]
    kept: list[Any] = []
    for v in vehicles:
        bb = getattr(v, "bbox", None)
        if bb is None:
            continue
        xyxy = (
            float(bb.x),
            float(bb.y),
            float(bb.x + bb.w),
            float(bb.y + bb.h),
        )
        if vehicle_in_anpr_roi(xyxy, roi_xyxy, overlap_min=overlap_min):
            kept.append(v)
    stats["roi_vehicles"] = len(kept)
    stats["ignored_outside_roi"] = max(0, total - len(kept))
    return kept, stats
