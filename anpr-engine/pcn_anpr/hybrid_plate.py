from __future__ import annotations

"""Hybrid plate detector: YOLO per vehicle, OpenCV only as fallback.

EVAL / production-safe: does not change OCR, API, or vehicle detection.
"""

import logging
import os
import time
from typing import Any

from pcn_anpr.interfaces import BoundingBox, PlateDetection, PlateDetector, VehicleDetection
from pcn_anpr.plate_modes import TimedPlateDetector, _summarize
from pcn_anpr.plate_quality import assess_plate_crop

logger = logging.getLogger(__name__)

_WIDE_IOU_THRESH = 0.4
_WIDE_CONF = 0.55
_WIDE_Y_FRAC = 0.48  # lower ~45–55% of vehicle (y from mid-ish to bottom)
# Small pad around host vehicle ROI only — never full-frame expansion.
_WIDE_PAD_FRAC = float(os.getenv("ANPR_WIDE_FALLBACK_PAD_FRAC", "0.02") or "0.02")
_MAX_SELECTED = 2
# Soft-quality-rejected YOLO crops retained for OCR (not hard geometry rejects).
_YOLO_OCR_FALLBACK_CLS = "yolo_quality_reject_ocr_fallback"
# Distant / low-quality hosts: skip wide OCR (truck rain/night MN2F2 case).
_WIDE_SKIP_VEHICLE_CONF = float(os.getenv("ANPR_WIDE_SKIP_VEHICLE_CONF", "0.48") or "0.48")
_WIDE_SKIP_AREA_RATIO = float(os.getenv("ANPR_WIDE_SKIP_AREA_RATIO", "0.045") or "0.045")
_WIDE_SKIP_MIN_SIDE = float(os.getenv("ANPR_WIDE_SKIP_MIN_SIDE", "160") or "160")


def _rank_key(p: PlateDetection, *, quality_score: float, rejected: bool) -> tuple[Any, ...]:
    """Higher is better. Prefer non-rejected, then conf + geometry."""
    bw = max(float(p.bbox.w), 1.0)
    bh = max(float(p.bbox.h), 1.0)
    aspect = bw / bh
    geom = 1.0 if 2.0 <= aspect <= 6.0 else (0.5 if 1.4 <= aspect <= 7.0 else 0.0)
    return (
        0 if rejected else 1,
        float(p.confidence) + 0.15 * quality_score + 0.10 * geom,
        bw * bh,
    )


def _xyxy(p: PlateDetection) -> tuple[float, float, float, float]:
    return (
        float(p.bbox.x),
        float(p.bbox.y),
        float(p.bbox.x + p.bbox.w),
        float(p.bbox.y + p.bbox.h),
    )


def _iou_xyxy(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return float(inter / union) if union > 0 else 0.0


def _clamp_xyxy_to_vehicle(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    vehicle_xyxy: tuple[float, float, float, float],
    fw: int,
    fh: int,
    pad_frac: float = _WIDE_PAD_FRAC,
) -> tuple[int, int, int, int] | None:
    """Clamp a box to host vehicle ROI + small padding (never full-frame)."""
    vx1, vy1, vx2, vy2 = vehicle_xyxy
    pad = float(fw) * pad_frac
    cx1 = max(0.0, vx1 - pad)
    cy1 = max(0.0, vy1 - pad)
    cx2 = min(float(fw), vx2 + pad)
    cy2 = min(float(fh), vy2 + pad)
    nx1 = int(max(cx1, min(x1, cx2)))
    ny1 = int(max(cy1, min(y1, cy2)))
    nx2 = int(max(cx1, min(x2, cx2)))
    ny2 = int(max(cy1, min(y2, cy2)))
    if (nx2 - nx1) < 40 or (ny2 - ny1) < 16:
        return None
    return nx1, ny1, nx2, ny2


def _vehicle_lower_band_xyxy(
    vehicle: VehicleDetection,
    *,
    fw: int,
    fh: int,
    y_frac: float = _WIDE_Y_FRAC,
    pad_frac: float = _WIDE_PAD_FRAC,
) -> tuple[int, int, int, int] | None:
    """Lower bumper band clamped to host vehicle ROI + small pad only."""
    vx1 = float(vehicle.bbox.x)
    vy1 = float(vehicle.bbox.y)
    vx2 = vx1 + float(vehicle.bbox.w)
    vy2 = vy1 + float(vehicle.bbox.h)
    vh = max(vy2 - vy1, 1.0)
    mid_y = vy1 + vh * y_frac
    # Start from vehicle lower band, then clamp to vehicle+pad (no frame wander).
    band = _clamp_xyxy_to_vehicle(
        vx1,
        mid_y,
        vx2,
        vy2,
        vehicle_xyxy=(vx1, vy1, vx2, vy2),
        fw=fw,
        fh=fh,
        pad_frac=pad_frac,
    )
    return band


def _is_distant_unreadable_host(
    vehicle: VehicleDetection,
    *,
    fw: int,
    fh: int,
    yolo_empty_or_hard_only: bool,
) -> bool:
    """True when wide OCR would mostly read background (low conf + tiny ROI)."""
    if not yolo_empty_or_hard_only:
        return False
    vconf = float(getattr(vehicle, "confidence", 0.0) or 0.0)
    if vconf >= _WIDE_SKIP_VEHICLE_CONF:
        return False
    vw = float(vehicle.bbox.w)
    vh = float(vehicle.bbox.h)
    frame_area = max(float(fw) * float(fh), 1.0)
    area_ratio = (vw * vh) / frame_area
    min_side = min(vw, vh)
    low_res = area_ratio < _WIDE_SKIP_AREA_RATIO or min_side < _WIDE_SKIP_MIN_SIDE
    return low_res


class HybridPlateDetector(PlateDetector):
    """Per-vehicle YOLO plate first; OpenCV only when YOLO has no usable crop.

    Full-frame ``detect(frame, None)`` returns ``[]`` — pipeline must not run a
    redundant full-frame OpenCV pass after all vehicles were processed.
    """

    detector_name = "hybrid"
    skip_full_frame_scan = True

    def __init__(self, yolo: PlateDetector, opencv: PlateDetector) -> None:
        self.yolo = TimedPlateDetector(yolo, name="yolo")
        self.opencv = TimedPlateDetector(opencv, name="opencv")
        self.last_meta: dict[str, Any] = {}
        self.last_vehicle_logs: list[dict[str, Any]] = []

    def initialize(self) -> bool:
        y_ok = self.yolo.initialize()
        o_ok = self.opencv.initialize()
        return y_ok or o_ok

    def warm_up(self) -> dict[str, Any]:
        out: dict[str, Any] = {"plate_detector": "hybrid"}
        if hasattr(self.yolo, "warm_up"):
            out["yolo"] = self.yolo.warm_up()
        if hasattr(self.opencv, "warm_up"):
            out["opencv"] = self.opencv.warm_up()
        return out

    def begin_frame(self) -> None:
        """Reset per-frame hybrid logs (call once before vehicle plate loop)."""
        self.last_vehicle_logs = []
        self.last_meta = {"mode": "hybrid", "used": None}

    def detect(self, frame: Any, vehicle: VehicleDetection | None = None) -> list[PlateDetection]:
        # Full-frame scan intentionally disabled in hybrid (per-vehicle only).
        if vehicle is None:
            self.last_meta = {
                "mode": "hybrid",
                "used": "skipped_full_frame",
                "count": 0,
                "note": "hybrid processes each YOLO vehicle ROI; full-frame OpenCV skipped",
            }
            logger.info("detector=hybrid skipped_full_frame=1")
            return []

        if frame is None or not hasattr(frame, "shape"):
            return []

        fh, fw = int(frame.shape[0]), int(frame.shape[1])
        vxyxy = (
            float(vehicle.bbox.x),
            float(vehicle.bbox.y),
            float(vehicle.bbox.x + vehicle.bbox.w),
            float(vehicle.bbox.y + vehicle.bbox.h),
        )
        started = time.perf_counter()
        yolo_raw = list(self.yolo.detect(frame, vehicle) or [])
        yolo_ms = self.yolo.last_time_ms

        scored: list[tuple[tuple[Any, ...], PlateDetection, dict[str, Any]]] = []
        for p in yolo_raw:
            x1 = int(p.bbox.x)
            y1 = int(p.bbox.y)
            x2 = int(p.bbox.x + p.bbox.w)
            y2 = int(p.bbox.y + p.bbox.h)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(fw, x2), min(fh, y2)
            crop = frame[y1:y2, x1:x2] if x2 > x1 and y2 > y1 else None
            assessment = assess_plate_crop(
                crop,
                bbox_xyxy=(float(x1), float(y1), float(x2), float(y2)),
                frame_shape=(fh, fw),
                vehicle_bbox=vxyxy,
            )
            reasons = list(assessment.reasons or [])
            # Soft rejects (few_char_blobs / chaotic_edges) vs hard (too_small / road).
            hard_reject = any(
                str(r).startswith("too_small") or str(r).startswith("bottom_road")
                for r in reasons
            )
            conf = float(p.confidence)
            quality_reject = bool(assessment.reject)
            q = {
                "reject": quality_reject,
                "hard_reject": hard_reject,
                "usable_despite_quality": False,
                "score": float(assessment.score),
                "reasons": reasons,
                "conf": conf,
                "bbox": [x1, y1, x2, y2],
            }
            scored.append(
                (
                    _rank_key(p, quality_score=q["score"], rejected=quality_reject),
                    p,
                    q,
                )
            )

        scored.sort(key=lambda item: item[0], reverse=True)
        # Usable = quality gate passed. Soft/hard reject → OpenCV (+ optional YOLO OCR retain).
        usable = [(p, q) for _k, p, q in scored if not q["reject"]]
        fallback_used = False
        opencv_raw: list[PlateDetection] = []
        opencv_ms = 0.0
        selected: list[PlateDetection] = []
        selected_source = "yolo"
        fallback_reason: str | None = None
        wide_fallback_used = False
        wide_bbox: list[int] | None = None
        wide_fallback_skipped: str | None = None
        plate_unreadable = False
        retained_yolo_ocr: PlateDetection | None = None

        if usable:
            # Best YOLO candidate(s) only — no OpenCV for this vehicle.
            best_p, best_q = usable[0]
            tagged = PlateDetection(
                bbox=best_p.bbox,
                confidence=float(best_p.confidence),
                class_name="yolo_plate",
                timing_ms=yolo_ms,
            )
            selected = [tagged]
            selected_meta = best_q
        else:
            # No usable YOLO (empty or quality-rejected) → OpenCV fallback.
            fallback_used = True
            selected_source = "opencv_fallback"
            soft_reject_yolo = bool(
                scored and scored[0][2].get("reject") and not scored[0][2].get("hard_reject")
            )
            yolo_empty_or_hard_only = (not scored) or bool(
                scored and scored[0][2].get("hard_reject")
            )
            if not scored:
                fallback_reason = "yolo_empty"
            elif scored[0][2].get("hard_reject"):
                fallback_reason = "yolo_hard_reject:" + ",".join(
                    scored[0][2].get("reasons") or []
                )
            else:
                fallback_reason = "yolo_quality_reject:" + ",".join(
                    (scored[0][2].get("reasons") or []) if scored else []
                )

            # P2: keep soft-rejected YOLO crop as an OCR candidate (pipeline skips
            # re-reject for this class_name so OCR still runs).
            if soft_reject_yolo:
                best_p = scored[0][1]
                retained_yolo_ocr = PlateDetection(
                    bbox=best_p.bbox,
                    confidence=float(best_p.confidence),
                    class_name=_YOLO_OCR_FALLBACK_CLS,
                    timing_ms=yolo_ms,
                )

            opencv_raw = list(self.opencv.detect(frame, vehicle) or [])
            opencv_ms = self.opencv.last_time_ms
            rejected_yolo_xyxy: tuple[float, float, float, float] | None = None
            if scored:
                rb = scored[0][2].get("bbox")
                if isinstance(rb, (list, tuple)) and len(rb) == 4:
                    rejected_yolo_xyxy = (
                        float(rb[0]),
                        float(rb[1]),
                        float(rb[2]),
                        float(rb[3]),
                    )

            opencv_sorted = sorted(
                opencv_raw, key=lambda p: float(p.confidence), reverse=True
            )
            good_opencv: list[PlateDetection] = []
            duplicate_opencv: list[PlateDetection] = []
            for p in opencv_sorted:
                # Keep OpenCV boxes that stay inside vehicle ROI (+ pad).
                px1, py1, px2, py2 = _xyxy(p)
                clamped = _clamp_xyxy_to_vehicle(
                    px1, py1, px2, py2, vehicle_xyxy=vxyxy, fw=fw, fh=fh
                )
                if clamped is None:
                    continue
                cx1, cy1, cx2, cy2 = clamped
                # Drop if clamp shrank the box drastically (mostly outside host).
                orig_a = max(1.0, (px2 - px1) * (py2 - py1))
                new_a = max(1.0, (cx2 - cx1) * (cy2 - cy1))
                if new_a / orig_a < 0.55:
                    continue
                clamped_det = PlateDetection(
                    bbox=BoundingBox(
                        float(cx1),
                        float(cy1),
                        float(cx2 - cx1),
                        float(cy2 - cy1),
                        float(p.confidence),
                    ),
                    confidence=float(p.confidence),
                    class_name="opencv_fallback",
                    timing_ms=opencv_ms,
                )
                if rejected_yolo_xyxy is None:
                    good_opencv.append(clamped_det)
                    continue
                if _iou_xyxy((cx1, cy1, cx2, cy2), rejected_yolo_xyxy) >= _WIDE_IOU_THRESH:
                    duplicate_opencv.append(clamped_det)
                else:
                    good_opencv.append(clamped_det)

            need_wide = (not opencv_sorted) or (
                rejected_yolo_xyxy is not None
                and len(good_opencv) == 0
                and len(duplicate_opencv) > 0
            )
            if not opencv_sorted:
                need_wide = True

            # Prefer retained YOLO OCR candidate first (P2).
            selected = []
            if retained_yolo_ocr is not None:
                selected.append(retained_yolo_ocr)

            for p in good_opencv:
                if len(selected) >= _MAX_SELECTED:
                    break
                selected.append(p)

            skip_wide = _is_distant_unreadable_host(
                vehicle,
                fw=fw,
                fh=fh,
                yolo_empty_or_hard_only=yolo_empty_or_hard_only,
            )
            if skip_wide and need_wide:
                wide_fallback_skipped = "plate_unreadable"
                plate_unreadable = True
                vconf = float(getattr(vehicle, "confidence", 0.0) or 0.0)
                vw = float(vehicle.bbox.w)
                vh = float(vehicle.bbox.h)
                area_ratio = (vw * vh) / max(float(fw) * float(fh), 1.0)
                msg = (
                    f"wide_fallback_skipped=plate_unreadable "
                    f"reason=low_conf_no_yolo_plate_low_res "
                    f"vehicle_conf={vconf:.3f} area_ratio={area_ratio:.4f} "
                    f"min_side={min(vw, vh):.0f} vehicle_bbox="
                    f"[{int(vxyxy[0])},{int(vxyxy[1])},{int(vxyxy[2])},{int(vxyxy[3])}]"
                )
                print(f"[ANPR HYBRID] {msg}", flush=True)
                logger.info(msg)
                need_wide = False

            if need_wide and len(selected) < _MAX_SELECTED:
                band = _vehicle_lower_band_xyxy(vehicle, fw=fw, fh=fh)
                if band is not None:
                    bx1, by1, bx2, by2 = band
                    wide_fallback_used = True
                    wide_bbox = [bx1, by1, bx2, by2]
                    selected.append(
                        PlateDetection(
                            bbox=BoundingBox(
                                float(bx1),
                                float(by1),
                                float(bx2 - bx1),
                                float(by2 - by1),
                                _WIDE_CONF,
                            ),
                            confidence=_WIDE_CONF,
                            class_name="opencv_fallback_wide",
                            timing_ms=opencv_ms,
                        )
                    )
                    print(
                        f"[ANPR HYBRID] wide_fallback bbox={wide_bbox} "
                        f"host_vehicle_bbox="
                        f"[{int(vxyxy[0])},{int(vxyxy[1])},{int(vxyxy[2])},{int(vxyxy[3])}]",
                        flush=True,
                    )
                    logger.info(
                        "wide_fallback bbox=%s host_vehicle_bbox=%s",
                        wide_bbox,
                        [int(vxyxy[0]), int(vxyxy[1]), int(vxyxy[2]), int(vxyxy[3])],
                    )
                elif not selected and duplicate_opencv:
                    selected.append(duplicate_opencv[0])
            elif not selected and duplicate_opencv and not need_wide:
                selected.append(duplicate_opencv[0])

            selected = selected[:_MAX_SELECTED]
            if retained_yolo_ocr is not None and selected and selected[0] is retained_yolo_ocr:
                if len(selected) == 1:
                    selected_source = _YOLO_OCR_FALLBACK_CLS
                elif any(
                    getattr(p, "class_name", "") == "opencv_fallback_wide" for p in selected
                ):
                    selected_source = f"{_YOLO_OCR_FALLBACK_CLS}+opencv_fallback_wide"
                else:
                    selected_source = f"{_YOLO_OCR_FALLBACK_CLS}+opencv_fallback"
            elif selected and any(
                getattr(p, "class_name", "") == "opencv_fallback_wide" for p in selected
            ):
                if len(selected) == 1:
                    selected_source = "opencv_fallback_wide"
                else:
                    selected_source = "opencv_fallback+wide"
            elif not selected and plate_unreadable:
                selected_source = "plate_unreadable"
            selected_meta = scored[0][2] if scored else None

        elapsed = (time.perf_counter() - started) * 1000.0
        vehicle_id = getattr(vehicle, "track_id", None)
        if vehicle_id is None:
            vehicle_id = getattr(vehicle, "vehicle_id", None)
        vlog = {
            "vehicle_id": vehicle_id,
            "vehicle_bbox": [int(vxyxy[0]), int(vxyxy[1]), int(vxyxy[2]), int(vxyxy[3])],
            "vehicle_label": getattr(vehicle, "label", None),
            "vehicle_conf": float(getattr(vehicle, "confidence", 0.0) or 0.0),
            "yolo_plate_candidates": len(yolo_raw),
            "yolo_candidate_details": [q for _k, _p, q in scored],
            "yolo_selected": selected_meta if selected_source == "yolo" else None,
            "yolo_best_rejected": selected_meta if selected_source != "yolo" else None,
            "yolo_selected_conf": (
                float(selected[0].confidence) if selected and selected_source == "yolo" else None
            ),
            "yolo_best_conf": (float(scored[0][2]["conf"]) if scored else None),
            "yolo_ocr_fallback_retained": retained_yolo_ocr is not None,
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "wide_fallback_used": wide_fallback_used,
            "wide_fallback_skipped": wide_fallback_skipped,
            "plate_unreadable": plate_unreadable,
            "wide_bbox": wide_bbox,
            "opencv_candidates": len(opencv_raw) if fallback_used else 0,
            "opencv_detections": _summarize(opencv_raw) if fallback_used else [],
            "selected_source": selected_source,
            "final_plates_n": len(selected),
            "final_plate_bboxes": [
                [
                    int(p.bbox.x),
                    int(p.bbox.y),
                    int(p.bbox.x + p.bbox.w),
                    int(p.bbox.y + p.bbox.h),
                ]
                for p in selected
            ],
            "yolo_ms": round(yolo_ms, 2),
            "opencv_ms": round(opencv_ms, 2),
            "hybrid_ms": round(elapsed, 2),
        }
        self.last_vehicle_logs.append(vlog)
        if len(self.last_vehicle_logs) > 32:
            self.last_vehicle_logs = self.last_vehicle_logs[-32:]

        self.last_meta = {
            "mode": "hybrid",
            "used": selected_source,
            "fallback_used": fallback_used,
            "wide_fallback_used": wide_fallback_used,
            "wide_fallback_skipped": wide_fallback_skipped,
            "plate_unreadable": plate_unreadable,
            "wide_bbox": wide_bbox,
            "yolo_count": len(yolo_raw),
            "opencv_count": len(opencv_raw) if fallback_used else 0,
            "selected_count": len(selected),
            "yolo_time_ms": yolo_ms,
            "opencv_time_ms": opencv_ms if fallback_used else 0.0,
            "vehicle_log": vlog,
        }

        print(
            f"[ANPR HYBRID] vehicle_id={vlog['vehicle_id']} "
            f"label={vlog['vehicle_label']} "
            f"yolo_candidates={vlog['yolo_plate_candidates']} "
            f"yolo_selected_conf={vlog['yolo_selected_conf']} "
            f"yolo_best_conf={vlog['yolo_best_conf']} "
            f"fallback_used={fallback_used} "
            f"fallback_reason={fallback_reason} "
            f"wide_fallback_used={wide_fallback_used} "
            f"wide_fallback_skipped={wide_fallback_skipped} "
            f"wide_bbox={wide_bbox} "
            f"host_vehicle_bbox={vlog['vehicle_bbox']} "
            f"opencv_candidates={vlog['opencv_candidates']} "
            f"final_n={len(selected)} source={selected_source}",
            flush=True,
        )
        logger.info(
            "detector=hybrid fallback=%s wide=%s wide_skip=%s yolo_n=%s opencv_n=%s selected=%s",
            fallback_used,
            wide_fallback_used,
            wide_fallback_skipped,
            len(yolo_raw),
            len(opencv_raw) if fallback_used else 0,
            len(selected),
        )
        return selected
