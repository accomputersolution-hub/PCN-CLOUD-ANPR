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
    assignment_method: str = "none"


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


# Move neural label helpers above filter so YOLO boxes use a higher area cap.
_NEURAL_VEHICLE_LABELS = frozenset({"car", "motorcycle", "bus", "truck", "bicycle"})


def _is_neural_vehicle_label(label: str) -> bool:
    return label.strip().lower() in _NEURAL_VEHICLE_LABELS


def filter_vehicle_detections(
    vehicles: Sequence[VehicleDetection],
    *,
    frame_shape: tuple[int, int],
    max_area_ratio: float = 0.35,
    neural_max_area_ratio: float = 0.55,
) -> list[VehicleDetection]:
    """Keep plausible vehicle boxes; drop full-frame / near-full-frame blobs.

    OpenCV contour heuristics and plate→vehicle synthesis can both emit boxes that
    cover most of the image. Those destroy plate association (every plate is
    "contained") and must never enter the multi-vehicle OCR loop.

    Neural/YOLO class labels (car/motorcycle/bus/truck) may legitimately cover more
    of a gate frame, so they use a higher area cap and skip OpenCV-oriented
    near-full-width contour rejection.
    """
    fh, fw = int(frame_shape[0] or 1), int(frame_shape[1] or 1)
    frame_area = float(max(1, fh * fw))
    kept: list[VehicleDetection] = []
    for v in vehicles:
        xyxy = _xyxy(v.bbox)
        area = _area(xyxy)
        ratio = area / frame_area
        bw = max(xyxy[2] - xyxy[0], 1.0)
        bh = max(xyxy[3] - xyxy[1], 1.0)
        label = str(getattr(v, "label", "") or "")
        neural = _is_neural_vehicle_label(label)
        limit = neural_max_area_ratio if neural else max_area_ratio
        if ratio >= limit:
            continue
        if bw >= fw * 0.92 and bh >= fh * 0.55:
            continue
        # OpenCV contour bands: wide + large — drop. YOLO boxes keep these.
        if (not neural) and bw >= fw * 0.85 and ratio >= 0.28:
            continue
        if bw < 40 or bh < 30:
            continue
        kept.append(v)
    return kept


def synthesize_vehicles_from_plates(
    plates: Sequence[PlateDetection],
    *,
    frame_shape: tuple[int, int],
    existing: Sequence[VehicleDetection] | None = None,
    max_new: int = 8,
    max_synth_area_ratio: float = 0.18,
    prefer_detector_hosts: bool = False,
) -> tuple[list[VehicleDetection], dict[str, Any]]:
    """Fallback: add local vehicle ROIs only for plates not already hosted.

    Detector boxes are primary. Synthesis runs only for tight plate candidates
    that are not well-contained in any surviving detector vehicle. Synthetic
    boxes are capped well below full-frame size and NMS'd against detectors.

    When ``prefer_detector_hosts`` is True (YOLO path) or existing boxes already
    carry neural class labels, synthesis is suppressed for plates that overlap a
    detector vehicle even modestly, and orphan YOLO boxes are never dropped.
    """
    fh, fw = int(frame_shape[0] or 1), int(frame_shape[1] or 1)
    frame_area = float(max(1, fh * fw))
    meta: dict[str, Any] = {
        "detector_in": len(existing or []),
        "detector_kept": 0,
        "plates_needing_host": 0,
        "synthesized": 0,
        "rejected_huge_synth": 0,
    }

    detector = filter_vehicle_detections(existing or [], frame_shape=frame_shape)
    # Tag detector boxes for logging (preserve existing non-synth labels).
    tagged_detector: list[VehicleDetection] = []
    for v in detector:
        label = str(getattr(v, "label", None) or "vehicle")
        if label.startswith("vehicle_from_plate"):
            label = "vehicle"
        tagged_detector.append(
            VehicleDetection(
                bbox=v.bbox,
                label=label,
                confidence=float(v.confidence),
                track_id=getattr(v, "track_id", None),
            )
        )
    meta["detector_kept"] = len(tagged_detector)

    neural_hosts = prefer_detector_hosts or any(
        _is_neural_vehicle_label(str(getattr(v, "label", "") or "")) for v in tagged_detector
    )
    meta["prefer_detector_hosts"] = bool(neural_hosts)
    # YOLO boxes often sit slightly outside the plate; accept lower containment.
    host_containment = 0.25 if neural_hosts else 0.40

    synthesized: list[VehicleDetection] = []
    for p in plates:
        bw, bh = float(p.bbox.w), float(p.bbox.h)
        if bw < 40 or bh < 12:
            continue
        aspect = bw / max(bh, 1.0)
        plate_area = bw * bh
        if aspect < 1.6 or aspect > 7.5:
            continue
        if plate_area > frame_area * 0.05:
            continue
        pxy = _xyxy(p.bbox)
        # Already hosted by a real detector vehicle → no synthetic needed.
        if any(_containment(pxy, _xyxy(d.bbox)) >= host_containment for d in tagged_detector):
            continue
        # Also skip if already covered by a prior synthetic (tight NMS later).
        if any(_containment(pxy, _xyxy(s.bbox)) >= 0.40 for s in synthesized):
            continue
        # With valid YOLO/neural vehicles present, only synth truly unhosted plates.
        # (Still allowed — do not blanket-disable when a distant plate has no host.)
        meta["plates_needing_host"] += 1

        cx = float(p.bbox.x) + bw * 0.5
        # Expand upward from the plate (rear of vehicle), keep width modest.
        vw = min(fw * 0.32, max(bw * 2.8, bw + 60))
        vh = min(fh * 0.38, max(bh * 4.5, bh + 90))
        x1 = max(0.0, cx - vw * 0.5)
        x2 = min(float(fw), cx + vw * 0.5)
        y2 = min(float(fh), float(p.bbox.y) + bh * 1.6)
        y1 = max(0.0, y2 - vh)
        # Re-cap if expansion still too large.
        syn_w, syn_h = x2 - x1, y2 - y1
        if syn_w * syn_h / frame_area > max_synth_area_ratio:
            # Shrink toward the plate center.
            vw2 = min(vw, fw * 0.25, bw * 3.0)
            vh2 = min(vh, fh * 0.30, bh * 4.0)
            x1 = max(0.0, cx - vw2 * 0.5)
            x2 = min(float(fw), cx + vw2 * 0.5)
            y2 = min(float(fh), float(p.bbox.y) + bh * 1.5)
            y1 = max(0.0, y2 - vh2)
            syn_w, syn_h = x2 - x1, y2 - y1
        if syn_w * syn_h / frame_area > max_synth_area_ratio:
            meta["rejected_huge_synth"] += 1
            continue
        if syn_w < 50 or syn_h < 40:
            continue
        conf = min(0.70, 0.35 + float(p.confidence) * 0.30)
        synthesized.append(
            VehicleDetection(
                bbox=BoundingBox(x1, y1, syn_w, syn_h, conf),
                label="vehicle_from_plate",
                confidence=conf,
            )
        )
        meta["synthesized"] += 1

    # Detector boxes win over overlapping synthetics (boost detector confidence).
    ranked: list[tuple[float, VehicleDetection]] = []
    for v in tagged_detector:
        ranked.append((float(v.confidence) + 1.0, v))  # detector priority
    for v in synthesized:
        ranked.append((float(v.confidence), v))
    ranked.sort(key=lambda item: item[0], reverse=True)

    kept: list[VehicleDetection] = []
    for _score, cand in ranked:
        c = _xyxy(cand.bbox)
        # Stricter overlap for synthetics so one car is not double-hosted.
        is_synth = str(getattr(cand, "label", "")).startswith("vehicle_from_plate")
        thresh = 0.30 if (is_synth and neural_hosts) else (0.35 if is_synth else 0.50)
        if any(_iou(c, _xyxy(k.bbox)) > thresh for k in kept):
            continue
        # Also drop synthetic fully inside a kept detector box.
        if is_synth:
            drop_inside = 0.40 if neural_hosts else 0.55
            if any(_containment(c, _xyxy(k.bbox)) >= drop_inside for k in kept):
                continue
        kept.append(cand)
        if len(kept) >= max_new:
            break

    meta["final_count"] = len(kept)
    meta["final_detector"] = sum(
        1 for v in kept if not str(getattr(v, "label", "")).startswith("vehicle_from_plate")
    )
    meta["final_synthetic"] = sum(
        1 for v in kept if str(getattr(v, "label", "")).startswith("vehicle_from_plate")
    )
    # Drop orphan OpenCV contour boxes that host no plate when synthetics cover
    # the scene. Never drop neural/YOLO detector vehicles — multi-vehicle UI
    # needs every valid YOLO box even without a plate yet.
    if meta["final_synthetic"] > 0 and plates and not neural_hosts:
        hosted: list[VehicleDetection] = []
        for v in kept:
            is_synth = str(getattr(v, "label", "")).startswith("vehicle_from_plate")
            if is_synth:
                hosted.append(v)
                continue
            vxy = _xyxy(v.bbox)
            if any(_containment(_xyxy(p.bbox), vxy) >= 0.35 for p in plates):
                hosted.append(v)
            else:
                meta["dropped_orphan_detector"] = int(meta.get("dropped_orphan_detector") or 0) + 1
        if hosted:
            kept = hosted
            meta["final_count"] = len(kept)
            meta["final_detector"] = sum(
                1
                for v in kept
                if not str(getattr(v, "label", "")).startswith("vehicle_from_plate")
            )
            meta["final_synthetic"] = sum(
                1
                for v in kept
                if str(getattr(v, "label", "")).startswith("vehicle_from_plate")
            )
    return kept, meta


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
    # Soften the penalty for lower-frame hosts (bumper cars in stock / phone photos
    # are often near full width but are still the real target).
    width_ratio = bw / float(fw)
    if width_ratio >= 0.78 or area_ratio >= 0.55:
        if (cy / fh) >= 0.55:
            size_score *= 0.55
            center_score *= 0.75
        else:
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


def _plate_host_boost(
    vehicle_xyxy: tuple[float, float, float, float],
    plates: Sequence[PlateDetection],
    *,
    frame_shape: tuple[int, int],
) -> float:
    """Boost vehicles that host compact mid/lower plate candidates (not watermarks)."""
    fh, fw = max(frame_shape[0], 1), max(frame_shape[1], 1)
    frame_area = float(fh * fw)
    best = 0.0
    for p in plates:
        bw, bh = float(p.bbox.w), float(p.bbox.h)
        if bw < 40 or bh < 12:
            continue
        aspect = bw / max(bh, 1.0)
        area_ratio = (bw * bh) / frame_area
        if aspect < 1.6 or aspect > 7.5 or area_ratio > 0.12:
            continue
        pxy = _xyxy(p.bbox)
        if _containment(pxy, vehicle_xyxy) < 0.40:
            continue
        _pcx, pcy = _center(pxy)
        # Top-band watermark / OSD crops must not elect a primary host.
        if pcy < fh * 0.22:
            continue
        score = 0.35 + min(1.0, float(p.confidence)) * 0.25
        if pcy >= fh * 0.45:
            score += 0.30
        if 2.0 <= aspect <= 6.0 and area_ratio <= 0.08:
            score += 0.20
        best = max(best, score)
    return best


def select_primary_vehicle(
    vehicles: Sequence[VehicleDetection],
    *,
    frame_shape: tuple[int, int],
    plates: Sequence[PlateDetection] | None = None,
) -> tuple[list[VehicleRef], int | None]:
    """Return vehicle refs ranked by prominence and the primary index."""
    if not vehicles:
        return [], None
    refs: list[VehicleRef] = []
    for i, v in enumerate(vehicles):
        xyxy = _xyxy(v.bbox)
        prom = vehicle_prominence(v, frame_shape=frame_shape)
        if plates:
            prom += _plate_host_boost(xyxy, plates, frame_shape=frame_shape)
        refs.append(
            VehicleRef(
                index=i,
                bbox_xyxy=xyxy,
                confidence=float(v.confidence),
                area=_area(xyxy),
                prominence=prom,
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
    frame_area = float(fh * fw)
    pcx, pcy = _center(pxy)
    diag = (fh**2 + fw**2) ** 0.5

    best_i: int | None = source_vehicle_index
    best_iou = 0.0
    best_contain = 0.0
    best_dist = 1.0
    assignment_method = "none"
    primary_idx = next((r.index for r in vehicle_refs if r.is_primary), None)

    if vehicle_refs:
        ranked: list[tuple[float, int, float, float, float, str]] = []
        for r in vehicle_refs:
            iou = _iou(pxy, r.bbox_xyxy)
            contain = _containment(pxy, r.bbox_xyxy)
            vcx, vcy = _center(r.bbox_xyxy)
            dist = (((pcx - vcx) ** 2 + (pcy - vcy) ** 2) ** 0.5) / diag
            area_ratio = float(r.area) / frame_area
            # Prefer containment, then IoU, then proximity; boost source vehicle from detect().
            score = contain * 2.0 + iou * 1.5 + (1.0 - dist) * 0.5
            if source_vehicle_index is not None and r.index == source_vehicle_index:
                score += 0.75
            # Huge hosts (near full-frame leftovers) must lose to tighter cars.
            if area_ratio >= 0.40:
                score -= 3.0
            elif area_ratio >= 0.28:
                score -= 1.0
            # Prefer compact hosts when containment is similar.
            score += (1.0 - min(1.0, area_ratio / 0.30)) * 0.35
            method = "containment" if contain >= 0.45 else ("iou" if iou >= 0.15 else "center_dist")
            ranked.append((score, r.index, iou, contain, dist, method))
        ranked.sort(reverse=True)
        _s, best_i, best_iou, best_contain, best_dist, assignment_method = ranked[0]
        # If barely overlapping anything, leave unassigned unless source was set.
        if best_contain < 0.15 and best_iou < 0.05 and source_vehicle_index is None:
            best_i = None
            assignment_method = "unassigned"
        elif source_vehicle_index is not None and best_i == source_vehicle_index:
            assignment_method = "source_vehicle"

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
    assoc = AssociatedPlate(
        detection=plate,
        vehicle_index=best_i,
        iou=float(best_iou),
        containment=float(best_contain),
        center_dist=float(best_dist),
        on_primary=on_primary,
        pre_ocr_score=float(pre),
        assignment_method=assignment_method,
    )
    return assoc


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
