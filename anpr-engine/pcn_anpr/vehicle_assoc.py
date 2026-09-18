from __future__ import annotations

"""Associate plate boxes with vehicles and pick the primary/target vehicle.

Manual ANPR scenes often contain a foreground motorcycle plus background cars.
OCR confidence alone must not pick an easier background plate.
"""

from dataclasses import dataclass
from typing import Any, Sequence

from pcn_anpr.interfaces import BoundingBox, PlateDetection, VehicleDetection


@dataclass(frozen=True)
class VehicleRef:
    index: int
    bbox_xyxy: tuple[float, float, float, float]
    confidence: float
    area: float
    prominence: float
    is_primary: bool = False
    track_id: str | None = None


@dataclass
class AssociatedPlate:
    detection: PlateDetection
    vehicle_index: int | None
    iou: float
    containment: float
    center_dist: float
    on_primary: bool
    pre_ocr_score: float


def _xyxy(b: BoundingBox) -> tuple[float, float, float, float]:
    return (float(b.x), float(b.y), float(b.x + b.w), float(b.y + b.h))


def _area(xyxy: tuple[float, float, float, float]) -> float:
    return max(0.0, xyxy[2] - xyxy[0]) * max(0.0, xyxy[3] - xyxy[1])


def _center(xyxy: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((xyxy[0] + xyxy[2]) * 0.5, (xyxy[1] + xyxy[3]) * 0.5)


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = _area(a) + _area(b) - inter
    return float(inter / union) if union > 0 else 0.0


def _containment(
    inner: tuple[float, float, float, float], outer: tuple[float, float, float, float]
) -> float:
    """Fraction of inner box area that lies inside outer."""
    ax1, ay1, ax2, ay2 = inner
    bx1, by1, bx2, by2 = outer
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    area = _area(inner)
    return float(inter / area) if area > 0 else 0.0


def vehicle_prominence(
    vehicle: VehicleDetection,
    *,
    frame_shape: tuple[int, int],
) -> float:
    """Higher = more likely the Manual-ANPR target vehicle (center / large / lower)."""
    fh, fw = max(frame_shape[0], 1), max(frame_shape[1], 1)
    xyxy = _xyxy(vehicle.bbox)
    area = _area(xyxy)
    area_ratio = area / float(fh * fw)
    bw = max(xyxy[2] - xyxy[0], 1.0)
    bh = max(xyxy[3] - xyxy[1], 1.0)
    cx, cy = _center(xyxy)
    # Distance from image center (0 = center).
    dx = abs(cx / fw - 0.5)
    dy = abs(cy / fh - 0.5)
    center_score = 1.0 - min(1.0, (dx * 1.2 + dy) / 1.1)
    # Prefer vehicles whose bbox covers the vertical mid/lower band (road targets).
    lower_bias = 1.0 - min(1.0, abs((cy / fh) - 0.55) / 0.55)
    size_score = min(1.0, area_ratio / 0.35)
    # Near-full-width contour bands are weak primaries when a tighter box exists.
    width_ratio = bw / float(fw)
    if width_ratio >= 0.78 or area_ratio >= 0.55:
        size_score *= 0.2
        center_score *= 0.5
    # Motorcycle rear boxes are taller / less wide than background cars.
    aspect = bw / bh
    motorcycle_bias = 0.0
    if 0.45 <= aspect <= 1.35:
        motorcycle_bias = 0.18
    elif 1.35 < aspect <= 2.4:
        motorcycle_bias = 0.08
    visibility = min(1.0, float(vehicle.confidence))
    return float(
        0.34 * size_score
        + 0.30 * center_score
        + 0.14 * lower_bias
        + 0.08 * visibility
        + motorcycle_bias
    )


def select_primary_vehicle(
    vehicles: Sequence[VehicleDetection],
    *,
    frame_shape: tuple[int, int],
) -> tuple[list[VehicleRef], int | None]:
    """Return vehicle refs ranked by prominence and the primary index."""
    if not vehicles:
        return [], None
    refs: list[VehicleRef] = []
    for i, v in enumerate(vehicles):
        xyxy = _xyxy(v.bbox)
        refs.append(
            VehicleRef(
                index=i,
                bbox_xyxy=xyxy,
                confidence=float(v.confidence),
                area=_area(xyxy),
                prominence=vehicle_prominence(v, frame_shape=frame_shape),
                track_id=getattr(v, "track_id", None),
            )
        )
    # Ignore full-frame fallback vehicles when real smaller vehicles exist.
    fh, fw = frame_shape
    frame_area = float(max(fh * fw, 1))
    real = [r for r in refs if r.area < frame_area * 0.92]
    pool = real or refs
    primary_idx = max(pool, key=lambda r: r.prominence).index
    out = [
        VehicleRef(
            index=r.index,
            bbox_xyxy=r.bbox_xyxy,
            confidence=r.confidence,
            area=r.area,
            prominence=r.prominence,
            is_primary=(r.index == primary_idx),
            track_id=r.track_id,
        )
        for r in refs
    ]
    return out, primary_idx


def associate_plate_to_vehicles(
    plate: PlateDetection,
    vehicle_refs: Sequence[VehicleRef],
    *,
    frame_shape: tuple[int, int],
    source_vehicle_index: int | None = None,
) -> AssociatedPlate:
    """Attach a plate to the best host vehicle (containment / IoU / nearest center)."""
    pxy = _xyxy(plate.bbox)
    fh, fw = max(frame_shape[0], 1), max(frame_shape[1], 1)
    pcx, pcy = _center(pxy)
    diag = (fh**2 + fw**2) ** 0.5

    best_i: int | None = source_vehicle_index
    best_iou = 0.0
    best_contain = 0.0
    best_dist = 1.0
    primary_idx = next((r.index for r in vehicle_refs if r.is_primary), None)

    if vehicle_refs:
        ranked: list[tuple[float, int, float, float, float]] = []
        for r in vehicle_refs:
            iou = _iou(pxy, r.bbox_xyxy)
            contain = _containment(pxy, r.bbox_xyxy)
            vcx, vcy = _center(r.bbox_xyxy)
            dist = (((pcx - vcx) ** 2 + (pcy - vcy) ** 2) ** 0.5) / diag
            # Prefer containment, then IoU, then proximity; boost source vehicle from detect().
            score = contain * 2.0 + iou * 1.5 + (1.0 - dist) * 0.5
            if source_vehicle_index is not None and r.index == source_vehicle_index:
                score += 0.75
            ranked.append((score, r.index, iou, contain, dist))
        ranked.sort(reverse=True)
        _s, best_i, best_iou, best_contain, best_dist = ranked[0]
        # If barely overlapping anything, leave unassigned unless source was set.
        if best_contain < 0.15 and best_iou < 0.05 and source_vehicle_index is None:
            best_i = None

    on_primary = best_i is not None and best_i == primary_idx
    # Pre-OCR ranking: primary association dominates size/OCR-ease.
    twoline_aspect = 0.0
    pw, ph = max(pxy[2] - pxy[0], 1.0), max(pxy[3] - pxy[1], 1.0)
    aspect = pw / ph
    if 1.3 <= aspect <= 3.2:
        twoline_aspect = 1.0 - min(1.0, abs(aspect - 2.2) / 2.2)
    center_dist = (((pcx / fw - 0.5) ** 2 + (pcy / fh - 0.5) ** 2) ** 0.5)
    pre = (
        (3.0 if on_primary else 0.0)
        + best_contain * 1.5
        + best_iou * 1.0
        + float(plate.confidence) * 0.8
        + min(1.0, (pw * ph) / (fw * fh) * 8.0) * 0.4
        + twoline_aspect * 0.35
        + (1.0 - min(1.0, center_dist * 1.5)) * 0.4
    )
    return AssociatedPlate(
        detection=plate,
        vehicle_index=best_i,
        iou=float(best_iou),
        containment=float(best_contain),
        center_dist=float(best_dist),
        on_primary=on_primary,
        pre_ocr_score=float(pre),
    )


def _twoline_geometry_score(row: dict[str, Any]) -> float:
    """Prefer compact two-line motorcycle plate boxes over wide single-line priors."""
    bbox = row.get("bbox") or row.get("padded_bbox") or [0, 0, 0, 0]
    if len(bbox) < 4:
        return 0.0
    w = max(float(bbox[2]) - float(bbox[0]), 1.0)
    h = max(float(bbox[3]) - float(bbox[1]), 1.0)
    aspect = w / h
    if 1.3 <= aspect <= 3.2:
        return 1.0 - min(1.0, abs(aspect - 2.2) / 2.2)
    return 0.0


def final_plate_rank_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Sort key for plates_out after OCR (higher is better via reverse=True).

    Among *valid* Indian plates, prefer:
    primary vehicle > two-line geometry > character/OCR quality > plate conf.
    Pattern / noise gates keep fragments and IU from becoming plates[0].
    """
    return (
        0 if row.get("non_plate_text") else 1,
        0 if row.get("marker_noise") else 1,
        # Valid Indian pattern must beat incomplete primary fragments (e.g. \"5687\").
        1 if row.get("matches_pattern") else 0,
        1 if row.get("ocr_confident") else 0,
        # Primary vehicle association beats an easier background plate.
        1 if row.get("on_primary_vehicle") else 0,
        1 if row.get("primary_roi_stage") else 0,
        _twoline_geometry_score(row),
        float(row.get("vehicle_containment") or 0.0),
        float(row.get("vehicle_iou") or 0.0),
        len(row.get("normalized_text") or "") if row.get("matches_pattern") else 0,
        float(row.get("ocr_confidence") or 0.0) if row.get("matches_pattern") else 0.0,
        float(row.get("plate_confidence") or 0.0),
        float(row.get("confidence") or 0.0),
    )


def vehicles_to_timing(refs: Sequence[VehicleRef]) -> list[dict[str, Any]]:
    return [
        {
            "index": r.index,
            "track_id": r.track_id,
            "bbox": [int(v) for v in r.bbox_xyxy],
            "confidence": round(r.confidence, 4),
            "area": round(r.area, 1),
            "prominence": round(r.prominence, 4),
            "is_primary": r.is_primary,
        }
        for r in refs
    ]
