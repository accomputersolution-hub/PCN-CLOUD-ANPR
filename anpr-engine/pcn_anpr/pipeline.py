from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from pcn_anpr.config import ANPRSettings, get_anpr_settings
from pcn_anpr.image_io import bbox_to_xyxy, load_bgr
from pcn_anpr.interfaces import (
    BoundingBox,
    OCRProvider,
    OCRResult,
    PipelineResult,
    PlateDetection,
    PlateDetector,
    VehicleDetector,
)
from pcn_anpr.mock_providers import MockOCRProvider, MockPlateDetector, MockVehicleDetector
from pcn_anpr.normalize import is_non_plate_text, is_plate_marker_noise, matches_indian_plate, stitch_plate_fragments, strip_plate
from pcn_anpr.ocr_ensemble import (
    MAX_PADDLE_CALLS_PER_IMAGE,
    MAX_PRIMARY_ROI_OCR_CALLS,
    MAX_SECONDARY_OCR_CALLS,
    MAX_STAGE1_OCR_CALLS,
    RESERVED_PRIMARY_OCR_CALLS,
    run_multipass_ocr,
)
from pcn_anpr.opencv_plate import expand_ind_strip_to_hsrp, expand_partial_plate_crop, merge_adjacent_plate_detections
from pcn_anpr.plate_quality import assess_plate_crop
from pcn_anpr.crop_refine import offset_bbox, refine_loose_plate_crop
from pcn_anpr.preprocess import crop_with_padding
from pcn_anpr.vehicle_assoc import (
    associate_plate_to_vehicles,
    final_plate_rank_key,
    select_primary_vehicle,
    vehicles_to_timing,
)
from pcn_anpr.vehicle_track import assign_frame_local_track_ids
from pcn_anpr.primary_roi_plate import detect_plates_in_primary_rois
from pcn_anpr.timing_log import merge_timing_into, print_step, print_timing_summary, step_timer
from pcn_anpr import ocr_perf

import logging

logger = logging.getLogger(__name__)


def _stage1_calls_used() -> int:
    sess = ocr_perf.get_session()
    if sess is None or not sess.enabled:
        return 0
    return sum(1 for c in sess.calls if str(c.stage).startswith("stage1"))


class ANPRPipeline:
    """Replaceable ANPR pipeline.

    Default providers are mocks for backward compatibility.
    Use ``pcn_anpr.factory.build_pipeline()`` for real OpenCV + PaddleOCR providers.
    """

    def __init__(
        self,
        vehicle_detector: VehicleDetector | None = None,
        plate_detector: PlateDetector | None = None,
        ocr: OCRProvider | None = None,
        settings: ANPRSettings | None = None,
    ) -> None:
        self.settings = settings or get_anpr_settings()
        self.vehicle_detector = vehicle_detector or MockVehicleDetector()
        self.plate_detector = plate_detector or MockPlateDetector()
        self.ocr = ocr or MockOCRProvider()
        # Optional live multi-frame tracker (set by LiveAnprWorker).
        self.vehicle_tracker = None

    def _ocr_scales(self) -> tuple[float, ...]:
        scales: list[float] = []
        if self.settings.ocr_upscale_2x:
            scales.append(2.0)
        if self.settings.ocr_upscale_3x:
            scales.append(3.0)
        return tuple(scales) or (2.0,)

    def _run_multipass_ocr_profiled(
        self,
        plate_crop_bgr: Any,
        *,
        stage: str,
        crop_id: str,
        bbox: list[int] | None = None,
        source: str = "",
        debug_dir: str | None = None,
        debug_prefix: str = "plate",
        live_mode: bool = False,
        early_exit_on_confident: bool = False,
        adaptive_fast_path: bool = False,
        aggressive_early_exit: bool = False,
    ):
        """Same multipass OCR behavior; attaches crop-scoped [OCR PERF] context."""
        with ocr_perf.ocr_crop_context(
            stage=stage,
            crop_id=crop_id,
            bbox=bbox,
            source=source,
            image=plate_crop_bgr,
        ):
            return run_multipass_ocr(
                plate_crop_bgr,
                self.ocr,
                min_ocr_confidence=self.settings.min_ocr_confidence,
                confusable_substitution=self.settings.confusable_substitution,
                debug_dir=debug_dir,
                debug_prefix=debug_prefix,
                scales=self._ocr_scales(),
                live_mode=live_mode,
                early_exit_on_confident=early_exit_on_confident,
                adaptive_fast_path=adaptive_fast_path,
                aggressive_early_exit=aggressive_early_exit,
            )

    def warm_up(self) -> dict[str, Any]:
        """Load OCR / plate-detector models once and run a dummy inference."""
        import numpy as np

        started = time.perf_counter()
        if hasattr(self.ocr, "initialize"):
            self.ocr.initialize()
        plate_warm: dict[str, Any] | None = None
        if hasattr(self.plate_detector, "warm_up"):
            try:
                plate_warm = dict(self.plate_detector.warm_up())
            except Exception:  # noqa: BLE001
                plate_warm = {"error": "plate_warmup_failed"}
        elif hasattr(self.plate_detector, "initialize"):
            try:
                self.plate_detector.initialize()
            except Exception:  # noqa: BLE001
                pass
        dummy = np.zeros((96, 192, 3), dtype=np.uint8)
        # Tiny white rectangle so detectors/OCR have something to chew on
        dummy[30:66, 40:152] = 240
        self._infer(dummy, debug_prefix="warmup", live_mode=True)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        ocr_init = getattr(self.ocr, "init_ms", None)
        return {
            "warmup_ms": elapsed_ms,
            "ocr_init_ms": ocr_init,
            "ocr_instance": getattr(self.ocr, "instance_id", id(self.ocr)),
            "construct_count": getattr(type(self.ocr), "_construct_count", None),
            "plate_detector_mode": getattr(self.settings, "plate_detector", "opencv"),
            "plate_warmup": plate_warm,
        }

    def process(self, frame: Any) -> PipelineResult:
        """Legacy single-best result (keeps Mock ANPR / edge oneshot working)."""
        started = time.perf_counter()
        try:
            # Fast path for mock oneshot (frame is None)
            if frame is None:
                vehicles = self.vehicle_detector.detect(frame) or []
                vehicle = vehicles[0] if vehicles else None
                plates = self.plate_detector.detect(frame, vehicle) or []
                plate = plates[0] if plates else None
                ocr = self.ocr.read(frame) if plate is not None else None
                elapsed = int((time.perf_counter() - started) * 1000)
                return PipelineResult(
                    vehicle=vehicle,
                    plate=plate,
                    ocr=ocr,
                    processing_ms=elapsed,
                    extras={"plate_detected": plate is not None},
                )

            payload = self._infer(frame)
            vehicle = plate = ocr = None
            if payload.get("vehicles"):
                from pcn_anpr.interfaces import BoundingBox, VehicleDetection

                v0 = payload["vehicles"][0]
                bb = v0["bbox"]
                vehicle = VehicleDetection(
                    bbox=BoundingBox(bb[0], bb[1], bb[2] - bb[0], bb[3] - bb[1], v0["confidence"]),
                    confidence=v0["confidence"],
                )
            if payload.get("plates"):
                from pcn_anpr.interfaces import BoundingBox, PlateDetection

                p0 = payload["plates"][0]
                bb = p0["bbox"]
                plate = PlateDetection(
                    bbox=BoundingBox(bb[0], bb[1], bb[2] - bb[0], bb[3] - bb[1], p0["plate_confidence"]),
                    confidence=p0["plate_confidence"],
                )
                ocr = OCRResult(
                    text=p0.get("normalized_text") or p0.get("raw_text") or "",
                    confidence=float(p0.get("ocr_confidence") or 0.0),
                    raw_text=p0.get("raw_text") or "",
                )
            elapsed = int((time.perf_counter() - started) * 1000)
            return PipelineResult(
                vehicle=vehicle,
                plate=plate,
                ocr=ocr,
                processing_ms=elapsed,
                extras={
                    "plate_detected": bool(payload.get("plate_detected")),
                    "ocr_confident": bool((payload.get("plates") or [{}])[0].get("ocr_confident")),
                },
            )
        except Exception as exc:  # noqa: BLE001 — never crash callers
            elapsed = int((time.perf_counter() - started) * 1000)
            return PipelineResult(
                vehicle=None,
                plate=None,
                ocr=None,
                processing_ms=elapsed,
                extras={"error": str(exc), "plate_detected": False},
            )

    def process_image(self, image_path: str | Path) -> dict[str, Any]:
        """Process a saved JPEG/PNG and return structured ANPR JSON (no DB events)."""
        started = step_timer()
        from pcn_anpr.image_io import describe_image_path, resolve_image_path

        supplied = image_path
        t_resolve = step_timer()
        path = resolve_image_path(image_path)
        resolve_ms = print_step("1) Image path resolve", t_resolve)
        base: dict[str, Any] = {
            "filename": path.name,
            "path": str(path),
            "supplied_path": str(supplied),
            "vehicles": [],
            "plates": [],
            "vehicle_detected": False,
            "plate_detected": False,
            "processing_ms": 0,
            "error": None,
        }
        try:
            if not path.exists() or not path.is_file():
                base["error"] = "image_not_found"
                base["path_debug"] = describe_image_path(supplied, path)
                base["processing_ms"] = int((step_timer() - started) * 1000)
                print_step("TOTAL process_image (image_not_found)", started)
                return base
            t_load = step_timer()
            frame = load_bgr(path)
            load_ms = print_step("2) Image loading / file decode (cv2.imread)", t_load)
            if frame is None:
                base["error"] = "invalid_image"
                base["path_debug"] = describe_image_path(supplied, path)
                base["processing_ms"] = int((step_timer() - started) * 1000)
                print_step("TOTAL process_image (invalid_image)", started)
                return base
            # Adaptive fast-path is for offline/manual process_image only.
            # Edge Agent / live callers use _infer(..., live_mode=True) unchanged.
            with ocr_perf.ocr_perf_session(enabled=True) as perf:
                # Snapshot whether Paddle is already loaded before this image.
                if hasattr(self.ocr, "_ocr"):
                    perf.note_model_ready(getattr(self.ocr, "_ocr", None) is not None)
                result = self._infer(frame, debug_prefix=path.stem, adaptive_fast_path=True)
                result["ocr_perf"] = perf.summary_dict()
                timing_out = result.setdefault("timing", {})
                if isinstance(timing_out, dict):
                    counts = perf.stage_call_counts()
                    timing_out["final_ocr_call_count"] = len(perf.calls)
                    timing_out["total_ocr_ms"] = round(perf.paddle_inference_ms, 2)
                    timing_out["stage1_calls"] = counts["stage1_calls"]
                    timing_out["primary_calls"] = counts["primary_calls"]
                    timing_out["secondary_calls"] = counts["secondary_calls"]
                    timing_out["duplicate_calls"] = counts["duplicate_calls"]
                    timing_out.setdefault(
                        "primary_reserved_budget", RESERVED_PRIMARY_OCR_CALLS
                    )
                    timing_out["final_early_exit_reason"] = timing_out.get(
                        "early_exit_reason"
                    )
                    print(
                        f"[OCR PERF] early_exit_reason={timing_out.get('early_exit_reason')} "
                        f"early_exit_after_call={timing_out.get('early_exit_after_call')} "
                        f"final_ocr_call_count={timing_out.get('final_ocr_call_count')} "
                        f"stage1_calls={timing_out.get('stage1_calls')} "
                        f"primary_calls={timing_out.get('primary_calls')} "
                        f"secondary_calls={timing_out.get('secondary_calls')} "
                        f"duplicate_calls={timing_out.get('duplicate_calls')} "
                        f"primary_reserved_budget={timing_out.get('primary_reserved_budget')} "
                        f"primary_partial_detected={timing_out.get('primary_partial_detected')} "
                        f"two_line_recovery_triggered={timing_out.get('two_line_recovery_triggered')} "
                        f"total_ocr_ms={timing_out.get('total_ocr_ms')}",
                        flush=True,
                    )
                perf.print_report()
            result["filename"] = path.name
            result["path"] = str(path)
            result["supplied_path"] = str(supplied)
            result["decoded_shape"] = list(frame.shape) if hasattr(frame, "shape") else None
            total_ms = (step_timer() - started) * 1000.0
            result["processing_ms"] = int(total_ms)
            timing = result.get("timing") or {}
            steps = {
                "image_path_resolve_ms": resolve_ms,
                "image_load_ms": load_ms,
                "vehicle_detect_ms": float(timing.get("vehicle_ms") or 0.0),
                "plate_detect_opencv_ms": float(timing.get("plate_ms") or 0.0),
                "preprocess_crop_ms": float(timing.get("crop_ms") or 0.0),
                "primary_roi_detect_ms": float(timing.get("primary_roi_ms") or 0.0),
                "ocr_ms": float(timing.get("ocr_ms") or 0.0),
                "postprocess_ms": float(timing.get("postprocess_ms") or 0.0),
            }
            merge_timing_into(result, steps, total_ms=total_ms)
            print_timing_summary(
                f"process_image summary - {path.name}",
                {
                    "Image path resolve": steps["image_path_resolve_ms"],
                    "Image loading / file decode": steps["image_load_ms"],
                    "Vehicle detection": steps["vehicle_detect_ms"],
                    "Plate detection (OpenCV)": steps["plate_detect_opencv_ms"],
                    "Preprocess / crop / resize": steps["preprocess_crop_ms"],
                    "Primary-ROI 2nd-stage detect": steps["primary_roi_detect_ms"],
                    "OCR text recognition": steps["ocr_ms"],
                    "Postprocess / stitch / rank": steps["postprocess_ms"],
                },
                total_ms=total_ms,
            )
            return result
        except Exception as exc:  # noqa: BLE001
            base["error"] = str(exc)
            base["processing_ms"] = int((step_timer() - started) * 1000)
            print_step("TOTAL process_image (exception)", started)
            return base

    def _infer(
        self,
        frame: Any,
        *,
        debug_prefix: str = "plate",
        live_mode: bool = False,
        adaptive_fast_path: bool = False,
    ) -> dict[str, Any]:
        vehicles_out: list[dict[str, Any]] = []
        plates_out: list[dict[str, Any]] = []
        stage_timing: dict[str, Any] = {
            "vehicle_ms": 0.0,
            "plate_ms": 0.0,
            "crop_ms": 0.0,
            "ocr_ms": 0.0,
            "postprocess_ms": 0.0,
            "adaptive_fast_path": adaptive_fast_path,
            "live_mode": live_mode,
            "plates_considered": 0,
            "plates_ocrd": 0,
        }

        if frame is None:
            return {
                "vehicles": [],
                "plates": [],
                "vehicle_detected": False,
                "plate_detected": False,
                "error": None,
                "timing": stage_timing,
            }

        vehicles = []
        t0 = step_timer()
        try:
            vehicles = self.vehicle_detector.detect(frame) or []
        except Exception:
            vehicles = []
        stage_timing["vehicle_ms"] = print_step("3) Vehicle detection", t0)

        # Stable track IDs: live uses IoU tracker; Manual uses frame-local IDs.
        if self.vehicle_tracker is not None and live_mode:
            try:
                # Primary index unknown yet — match by geometry first, mark primary after.
                vehicles = self.vehicle_tracker.update(vehicles, primary_index=None)
            except Exception:  # noqa: BLE001
                vehicles = assign_frame_local_track_ids(vehicles)
        else:
            vehicles = assign_frame_local_track_ids(vehicles)

        for i, v in enumerate(vehicles):
            x1, y1, x2, y2 = bbox_to_xyxy(v.bbox.x, v.bbox.y, v.bbox.w, v.bbox.h)
            vehicles_out.append(
                {
                    "index": i,
                    "track_id": v.track_id,
                    "label": v.label,
                    "confidence": float(v.confidence),
                    "bbox": [x1, y1, x2, y2],
                    "is_primary": False,
                }
            )

        plate_dets: list[PlateDetection] = []
        plate_sources: list[int | None] = []
        t0 = step_timer()
        try:
            if vehicles:
                for vi, v in enumerate(vehicles):
                    found = self.plate_detector.detect(frame, v) or []
                    for p in found:
                        plate_dets.append(p)
                        plate_sources.append(vi)
            if not plate_dets:
                plate_dets = self.plate_detector.detect(frame, None) or []
                plate_sources = [None] * len(plate_dets)
        except Exception:
            plate_dets = []
            plate_sources = []
        stage_timing["plate_ms"] = print_step(
            f"4) Plate detection OpenCV (candidates={len(plate_dets)})", t0
        )

        configured_mode = getattr(self.settings, "plate_detector", "opencv") or "opencv"
        stage_timing["plate_detector_mode"] = configured_mode
        stage_timing["plate_detector_used"] = configured_mode
        # Compare / AI-fallback modes may expose last_meta for evaluation timing.
        meta = getattr(self.plate_detector, "last_meta", None)
        if isinstance(meta, dict) and meta:
            stage_timing["plate_detector_mode"] = meta.get("mode") or configured_mode
            stage_timing["plate_detector_used"] = meta.get("used") or configured_mode
            stage_timing["plate_compare"] = {
                "used": meta.get("used"),
                "opencv_time_ms": meta.get("opencv_time_ms"),
                "ai_time_ms": meta.get("ai_time_ms"),
                "opencv_count": meta.get("opencv_count"),
                "ai_count": meta.get("ai_count"),
                "opencv_detections": meta.get("opencv_detections"),
                "ai_detections": meta.get("ai_detections"),
            }
            if meta.get("used") == "opencv_fallback":
                stage_timing["ai_detector_note"] = (
                    "AI plate detector returned no boxes (weights missing or inference disabled); "
                    "OpenCV plate detector used."
                )
                logger.warning(
                    "plate.ai_unavailable_using_opencv mode=%s ai_n=%s",
                    meta.get("mode"),
                    meta.get("ai_count"),
                )

        # Keep (detection, source_vehicle) pairs sorted by detector confidence.
        paired = list(zip(plate_dets, plate_sources))
        paired.sort(key=lambda item: (item[0].confidence, item[0].bbox.w * item[0].bbox.h), reverse=True)
        plate_dets = [p for p, _ in paired]
        plate_sources = [s for _, s in paired]

        # Offline: merge adjacent fragment boxes (split one-line / two-line plates)
        # so OCR can see the full registration before ranking.
        if not live_mode and len(plate_dets) >= 2:
            before = len(plate_dets)
            plate_dets = merge_adjacent_plate_detections(plate_dets)
            # Merged unions have no source vehicle — re-associate by geometry later.
            plate_sources = list(plate_sources) + [None] * max(0, len(plate_dets) - before)
            plate_sources = plate_sources[: len(plate_dets)]

        fh = fw = 0
        if hasattr(frame, "shape") and len(frame.shape) >= 2:
            fh, fw = int(frame.shape[0]), int(frame.shape[1])
        frame_shape = (fh, fw) if fh and fw else (1, 1)

        vehicle_refs, primary_idx = select_primary_vehicle(vehicles, frame_shape=frame_shape)
        # Refresh tracker primary flag after prominence selection.
        if self.vehicle_tracker is not None and live_mode and vehicles:
            try:
                vehicles = self.vehicle_tracker.update(vehicles, primary_index=primary_idx)
                vehicle_refs, primary_idx = select_primary_vehicle(vehicles, frame_shape=frame_shape)
                for i, v in enumerate(vehicles):
                    if i < len(vehicles_out):
                        vehicles_out[i]["track_id"] = v.track_id
                        vehicles_out[i]["bbox"] = [
                            int(v.bbox.x),
                            int(v.bbox.y),
                            int(v.bbox.x + v.bbox.w),
                            int(v.bbox.y + v.bbox.h),
                        ]
            except Exception:  # noqa: BLE001
                pass
        stage_timing["vehicle_refs"] = vehicles_to_timing(vehicle_refs)
        stage_timing["primary_vehicle_index"] = primary_idx
        if primary_idx is not None and primary_idx < len(vehicles_out):
            vehicles_out[primary_idx]["is_primary"] = True
            if primary_idx < len(vehicle_refs):
                vehicles_out[primary_idx]["track_id"] = vehicle_refs[primary_idx].track_id
            stage_timing["selected_vehicle"] = {
                "index": primary_idx,
                "track_id": vehicles_out[primary_idx].get("track_id"),
                "bbox": vehicles_out[primary_idx]["bbox"],
                "prominence": next(
                    (r.prominence for r in vehicle_refs if r.index == primary_idx), 0.0
                ),
            }

        # Pre-OCR quality gate + vehicle association.
        candidate_report: list[dict[str, Any]] = []
        associated: list[Any] = []
        for p, src_vi in zip(plate_dets, plate_sources):
            x1, y1, x2, y2 = bbox_to_xyxy(p.bbox.x, p.bbox.y, p.bbox.w, p.bbox.h)
            assoc = associate_plate_to_vehicles(
                p,
                vehicle_refs,
                frame_shape=frame_shape,
                source_vehicle_index=src_vi,
            )
            host_bbox = None
            if assoc.vehicle_index is not None and assoc.vehicle_index < len(vehicle_refs):
                host_bbox = vehicle_refs[assoc.vehicle_index].bbox_xyxy
            preview = None
            if fh > 0 and fw > 0:
                xa, ya = max(0, int(x1)), max(0, int(y1))
                xb, yb = min(fw, int(x2)), min(fh, int(y2))
                if xb > xa and yb > ya:
                    preview = frame[ya:yb, xa:xb]
            assessment = assess_plate_crop(
                preview,
                bbox_xyxy=(float(x1), float(y1), float(x2), float(y2)),
                frame_shape=(fh, fw) if fh and fw else None,
                vehicle_bbox=host_bbox,
            )
            row = {
                "bbox": [int(x1), int(y1), int(x2), int(y2)],
                "confidence": round(float(p.confidence), 4),
                "quality": assessment.as_dict(),
                "vehicle_index": assoc.vehicle_index,
                "on_primary_vehicle": assoc.on_primary,
                "vehicle_iou": round(assoc.iou, 4),
                "vehicle_containment": round(assoc.containment, 4),
                "pre_ocr_score": round(assoc.pre_ocr_score, 4),
            }
            candidate_report.append(row)
            if assessment.reject:
                logger.info(
                    "plate.candidate_rejected bbox=%s conf=%.3f reasons=%s",
                    row["bbox"],
                    p.confidence,
                    assessment.reasons,
                )
                continue
            blended = float(min(0.95, 0.55 * float(p.confidence) + 0.45 * assessment.score))
            # Boost primary-vehicle plates so they OCR first (adaptive early-exit).
            if assoc.on_primary:
                blended = min(0.95, blended + 0.08)
            det = PlateDetection(
                bbox=BoundingBox(p.bbox.x, p.bbox.y, p.bbox.w, p.bbox.h, blended),
                confidence=blended,
                class_name=getattr(p, "class_name", None) or "license_plate",
                timing_ms=getattr(p, "timing_ms", None),
            )
            assoc.detection = det
            assoc.pre_ocr_score = associate_plate_to_vehicles(
                det,
                vehicle_refs,
                frame_shape=frame_shape,
                source_vehicle_index=assoc.vehicle_index,
            ).pre_ocr_score
            associated.append(assoc)

        stage_timing["plate_candidates"] = candidate_report
        if associated:
            associated.sort(key=lambda a: (a.pre_ocr_score, a.detection.confidence), reverse=True)
            plate_dets = [a.detection for a in associated]
            plate_assoc_meta = associated
        else:
            logger.warning(
                "plate.all_candidates_rejected n=%s mode=%s",
                len(candidate_report),
                stage_timing.get("plate_detector_mode") or getattr(self.settings, "plate_detector", "opencv"),
            )
            plate_dets = []
            plate_assoc_meta = []

        # Offline: already-cropped plate images (wide short aspect) — include full frame
        # so fragment-only OpenCV proposals cannot strand a clear registration.
        # Do NOT prepend full-frame on small scene photos that already have plate boxes:
        # motorcycle rears (e.g. two-line MH02G/D7249) get ruined by lower-band refine.
        if not live_mode and hasattr(frame, "shape") and len(frame.shape) >= 2:
            fh, fw = int(frame.shape[0]), int(frame.shape[1])
            aspect = fw / max(fh, 1)
            plate_like = aspect >= 2.0 and fh <= 480
            has_strong_plate = any(float(p.confidence) >= 0.55 for p in plate_dets)
            if plate_like or (not plate_dets and fh * fw <= 640 * 480):
                full = PlateDetection(
                    bbox=BoundingBox(0.0, 0.0, float(fw), float(fh), 0.93 if plate_like else 0.55),
                    confidence=0.93 if plate_like else 0.55,
                )
                plate_dets = [full] + list(plate_dets)
                plate_assoc_meta = [
                    associate_plate_to_vehicles(full, vehicle_refs, frame_shape=frame_shape)
                ] + list(plate_assoc_meta)
            elif not has_strong_plate and fh * fw <= 640 * 480:
                full = PlateDetection(
                    bbox=BoundingBox(0.0, 0.0, float(fw), float(fh), 0.45),
                    confidence=0.45,
                )
                plate_dets = list(plate_dets) + [full]
                plate_assoc_meta = list(plate_assoc_meta) + [
                    associate_plate_to_vehicles(full, vehicle_refs, frame_shape=frame_shape)
                ]
        # Live: one best plate candidate per vehicle track (independent results).
        # Manual adaptive: PRIMARY first, then defer background until primary ROI runs.
        _secondary_pairs_held: list[tuple[Any, Any]] = []
        if live_mode:
            seen_vi: set[Any] = set()
            filtered_dets: list[PlateDetection] = []
            filtered_meta: list[Any] = []
            limit = max(1, min(3, len(vehicle_refs) or 1))
            for det, meta in zip(plate_dets, plate_assoc_meta):
                vi = meta.vehicle_index if meta is not None else None
                key = vi if vi is not None else ("geom", int(det.bbox.x), int(det.bbox.y))
                if key in seen_vi:
                    continue
                seen_vi.add(key)
                filtered_dets.append(det)
                filtered_meta.append(meta)
                if len(filtered_dets) >= limit:
                    break
            plate_dets = filtered_dets
            plate_assoc_meta = filtered_meta
        elif adaptive_fast_path:
            # Split primary vs background; OCR primary first with a tight Stage-1 budget.
            primary_pairs = [
                (d, m)
                for d, m in zip(plate_dets, plate_assoc_meta)
                if m is not None and m.on_primary
            ]
            secondary_pairs = [
                (d, m)
                for d, m in zip(plate_dets, plate_assoc_meta)
                if m is None or not m.on_primary
            ]
            # Keep a few primary candidates; park secondary for optional later pass.
            primary_pairs = primary_pairs[:3] or list(zip(plate_dets[:2], plate_assoc_meta[:2]))
            stage_timing["stage1_primary_candidates"] = len(primary_pairs)
            stage_timing["stage1_secondary_parked"] = len(secondary_pairs)
            stage_timing["secondary_plate_queue"] = [
                {
                    "bbox": [
                        int(d.bbox.x),
                        int(d.bbox.y),
                        int(d.bbox.x + d.bbox.w),
                        int(d.bbox.y + d.bbox.h),
                    ],
                    "on_primary": bool(m.on_primary) if m else False,
                    "pre_ocr_score": round(float(m.pre_ocr_score), 4) if m else 0.0,
                }
                for d, m in secondary_pairs[:8]
            ]
            # Stage-1 only processes primary now; secondary runs after ROI if needed.
            plate_dets = [d for d, _ in primary_pairs]
            plate_assoc_meta = [m for _, m in primary_pairs]
            _secondary_pairs_held = secondary_pairs
        else:
            _secondary_pairs_held = []
        stage_timing["plates_considered"] = len(plate_dets)
        stage_timing["primary_reserved_budget"] = RESERVED_PRIMARY_OCR_CALLS
        stage_timing["max_stage1_ocr_calls"] = MAX_STAGE1_OCR_CALLS
        if plate_dets:
            best = plate_dets[0]
            bx1, by1, bx2, by2 = bbox_to_xyxy(best.bbox.x, best.bbox.y, best.bbox.w, best.bbox.h)
            stage_timing["selected_bbox"] = [int(bx1), int(by1), int(bx2), int(by2)]
            stage_timing["selected_plate_confidence"] = round(float(best.confidence), 4)
            if plate_assoc_meta:
                stage_timing["selected_plate_vehicle"] = {
                    "vehicle_index": plate_assoc_meta[0].vehicle_index,
                    "on_primary": plate_assoc_meta[0].on_primary,
                    "pre_ocr_score": round(plate_assoc_meta[0].pre_ocr_score, 4),
                }

        t_ocr_stage1 = step_timer()
        ocr_ms_before = float(stage_timing.get("ocr_ms") or 0.0)
        crop_ms_before = float(stage_timing.get("crop_ms") or 0.0)
        for idx, p in enumerate(plate_dets):
            # Hard Stage-1 budget — never consume primary reserved calls.
            if adaptive_fast_path and not live_mode:
                if _stage1_calls_used() >= MAX_STAGE1_OCR_CALLS:
                    stage_timing["stage1_budget_exhausted"] = True
                    break
            if p.confidence < self.settings.min_plate_confidence:
                continue
            assoc_meta = plate_assoc_meta[idx] if idx < len(plate_assoc_meta) else None
            x1, y1, x2, y2 = bbox_to_xyxy(p.bbox.x, p.bbox.y, p.bbox.w, p.bbox.h)
            t_crop = step_timer()
            crop, padded_box = crop_with_padding(
                frame,
                x1,
                y1,
                x2,
                y2,
                pad_ratio=self.settings.plate_pad_ratio,
            )
            stage_timing["crop_ms"] = round(
                stage_timing["crop_ms"] + (step_timer() - t_crop) * 1000.0,
                2,
            )
            if crop is None:
                continue

            # Loose vehicle-front crops (grille + bumper): tighten onto the plate
            # before OCR so chrome slots are not read as registration text.
            # Skip refine when the detector box is already a compact plate region
            # (two-line motorcycle plates must keep both rows).
            refine_meta: dict[str, Any] | None = None
            ocr_crop = crop
            orig_padded_box = padded_box
            det_w = max(1, int(x2 - x1))
            det_h = max(1, int(y2 - y1))
            det_aspect = det_w / max(det_h, 1)
            already_tight_plate = (
                1.6 <= det_aspect <= 5.5
                and det_h <= 140
                and det_w <= 520
                and (fh <= 0 or (det_w * det_h) <= (fh * fw) * 0.22)
            )
            if not live_mode and not already_tight_plate:
                refined, inner_xyxy, refine_meta = refine_loose_plate_crop(crop)
                if refined is not None and inner_xyxy is not None:
                    ocr_crop = refined
                    abs_box = offset_bbox(padded_box, inner_xyxy)
                    padded_box = abs_box
                    x1, y1, x2, y2 = abs_box
                    stage_timing["crop_refined"] = True
                    stage_timing["crop_refine"] = refine_meta
                    logger.info(
                        "plate.crop_refined reason=%s xyxy=%s",
                        refine_meta.get("reason"),
                        list(inner_xyxy),
                    )
            elif already_tight_plate:
                stage_timing["crop_refine"] = {"refined": False, "reason": "already_tight_plate"}

            # Live never writes OCR debug crops unless settings explicitly keep them AND not live
            debug_dir = None
            if self.settings.ocr_save_debug_crops and not live_mode:
                debug_dir = self.settings.ocr_debug_dir
            ensemble = self._run_multipass_ocr_profiled(
                ocr_crop,
                stage="stage1",
                crop_id=f"plate{idx}",
                bbox=[x1, y1, x2, y2],
                source="stage1_det",
                debug_dir=debug_dir,
                debug_prefix=f"{debug_prefix}_{idx}",
                live_mode=live_mode,
                early_exit_on_confident=live_mode or adaptive_fast_path,
                adaptive_fast_path=adaptive_fast_path and not live_mode,
            )
            # Loose front crops: if OCR is weak / short false embed, retry lower bumper band.
            from pcn_anpr.crop_refine import is_loose_vehicle_front_crop

            ens_len = len(strip_plate(ensemble.normalized_text or ""))
            need_lower_retry = (
                not live_mode
                and crop is not None
                and hasattr(crop, "shape")
                and is_loose_vehicle_front_crop(crop)
                and (
                    not (ensemble.ocr_confident and ensemble.matches_pattern)
                    or ens_len <= 8
                )
            )
            if need_lower_retry:
                ch, cw = int(crop.shape[0]), int(crop.shape[1])
                if ch >= 90:
                    y0 = int(ch * 0.52)
                    band = crop[y0:, int(cw * 0.05) : int(cw * 0.95)]
                    band_ens = self._run_multipass_ocr_profiled(
                        band,
                        stage="stage1_lowerband",
                        crop_id=f"plate{idx}_lowerband",
                        bbox=[x1, y1, x2, y2],
                        source="lowerband_retry",
                        debug_dir=debug_dir,
                        debug_prefix=f"{debug_prefix}_{idx}_lowerband",
                        live_mode=False,
                        early_exit_on_confident=True,
                        adaptive_fast_path=True,
                    )
                    band_len = len(strip_plate(band_ens.normalized_text or ""))
                    take_band = band_ens.matches_pattern and (
                        not ensemble.matches_pattern
                        or band_len > ens_len
                        or (
                            band_len >= ens_len
                            and float(band_ens.ocr_confidence) >= float(ensemble.ocr_confidence)
                        )
                    )
                    if take_band:
                        ensemble = band_ens
                        stage_timing["crop_refine_lowerband_retry"] = True
                        parent = orig_padded_box
                        x1, y1, x2, y2 = (
                            int(parent[0] + cw * 0.05),
                            int(parent[1] + y0),
                            int(parent[0] + cw * 0.95),
                            int(parent[3]),
                        )
                        padded_box = (x1, y1, x2, y2)
            ocr_ms_accum = float((ensemble.timing or {}).get("ocr_total_ms") or 0.0)
            # Offline: if we only read HSRP IND / non-plate text, expand right and OCR once more.
            crop_text = ensemble.normalized_text or ensemble.raw_text or ""
            if (
                adaptive_fast_path
                and not live_mode
                and not ensemble.ocr_confident
                and is_non_plate_text(crop_text)
                and _stage1_calls_used() < MAX_STAGE1_OCR_CALLS
            ):
                expanded, exp_box = expand_ind_strip_to_hsrp(frame, x1, y1, x2, y2)
                if expanded is not None:
                    expanded_ensemble = self._run_multipass_ocr_profiled(
                        expanded,
                        stage="stage1_hsrp",
                        crop_id=f"plate{idx}_hsrp",
                        bbox=list(exp_box) if exp_box else [x1, y1, x2, y2],
                        source="hsrp_expand",
                        debug_dir=debug_dir,
                        debug_prefix=f"{debug_prefix}_{idx}_hsrp",
                        live_mode=False,
                        early_exit_on_confident=True,
                        adaptive_fast_path=True,
                    )
                    ocr_ms_accum += float((expanded_ensemble.timing or {}).get("ocr_total_ms") or 0.0)
                    if expanded_ensemble.ocr_confident or (
                        expanded_ensemble.matches_pattern
                        and not is_non_plate_text(
                            expanded_ensemble.normalized_text or expanded_ensemble.raw_text or ""
                        )
                    ):
                        ensemble = expanded_ensemble
                        padded_box = exp_box
                        x1, y1, x2, y2 = exp_box
            # Offline: partial plate fragments (TN51 / Y6552 / bottom line D7249) —
            # widen crop (and grow upward for likely two-line bottoms) then retry once.
            # Skip state+RTO-only top lines (MH12) — primary-ROI recovers the full
            # two-line plate without another 5 widen variants.
            elif (
                adaptive_fast_path
                and not live_mode
                and not ensemble.ocr_confident
                and not is_non_plate_text(crop_text)
                and strip_plate(crop_text)
                and not matches_indian_plate(strip_plate(crop_text))
                and not re.match(r"^[A-Z]{2}[0-9]{1,2}$", strip_plate(crop_text))
                and len(strip_plate(crop_text)) >= 7
                and _stage1_calls_used() < MAX_STAGE1_OCR_CALLS
            ):
                compact = strip_plate(crop_text)
                # Digit-heavy short reads are often the lower row of a two-line bike plate.
                digit_heavy = sum(ch.isdigit() for ch in compact) >= max(3, len(compact) // 2)
                grow_up = 1.35 if digit_heavy and len(compact) <= 6 else None
                expanded, exp_box = expand_partial_plate_crop(
                    frame, x1, y1, x2, y2, grow_up=grow_up
                )
                if expanded is not None:
                    expanded_ensemble = self._run_multipass_ocr_profiled(
                        expanded,
                        stage="stage1_wide",
                        crop_id=f"plate{idx}_wide",
                        bbox=list(exp_box) if exp_box else [x1, y1, x2, y2],
                        source="partial_expand",
                        debug_dir=debug_dir,
                        debug_prefix=f"{debug_prefix}_{idx}_wide",
                        live_mode=False,
                        early_exit_on_confident=True,
                        adaptive_fast_path=True,
                    )
                    ocr_ms_accum += float((expanded_ensemble.timing or {}).get("ocr_total_ms") or 0.0)
                    if expanded_ensemble.ocr_confident or (
                        expanded_ensemble.matches_pattern
                        and not is_non_plate_text(
                            expanded_ensemble.normalized_text or expanded_ensemble.raw_text or ""
                        )
                    ):
                        ensemble = expanded_ensemble
                        padded_box = exp_box
                        x1, y1, x2, y2 = exp_box

            ocr_timing = ensemble.timing or {}
            stage_timing["ocr_ms"] = round(stage_timing["ocr_ms"] + ocr_ms_accum, 2)
            stage_timing["plates_ocrd"] = int(stage_timing["plates_ocrd"]) + 1

            result_text = ensemble.normalized_text or ensemble.raw_text or ""
            non_plate = is_non_plate_text(result_text)
            marker_only = is_plate_marker_noise(result_text)
            # Pattern match dominates; OCR confidence never elevates watermark/legend text.
            if ensemble.matches_pattern and ensemble.ocr_confident and not non_plate:
                combined = float(p.confidence) * 0.35 + ensemble.ocr_confidence * 0.65
            elif ensemble.matches_pattern and not non_plate:
                combined = float(p.confidence) * 0.30 + ensemble.ocr_confidence * 0.40
            else:
                # Non-plate / incomplete OCR — geometry only (never win on OCR conf).
                combined = float(p.confidence) * 0.15
            plates_out.append(
                {
                    "raw_text": ensemble.raw_text,
                    "normalized_text": None if non_plate else ensemble.normalized_text,
                    "normalized_plate": None if non_plate else ensemble.normalized_text,
                    "ocr_confident": bool(ensemble.ocr_confident and not non_plate),
                    "confidence": round(combined, 4),
                    "ocr_confidence": round(ensemble.ocr_confidence, 4),
                    "plate_confidence": round(float(p.confidence), 4),
                    "matches_pattern": bool(ensemble.matches_pattern and not non_plate),
                    "marker_noise": marker_only,
                    "non_plate_text": non_plate,
                    "bbox": [x1, y1, x2, y2],
                    "padded_bbox": list(padded_box),
                    "vehicle_index": None if assoc_meta is None else assoc_meta.vehicle_index,
                    "track_id": (
                        vehicle_refs[assoc_meta.vehicle_index].track_id
                        if assoc_meta is not None
                        and assoc_meta.vehicle_index is not None
                        and 0 <= assoc_meta.vehicle_index < len(vehicle_refs)
                        else None
                    ),
                    "on_primary_vehicle": bool(assoc_meta.on_primary) if assoc_meta else False,
                    "vehicle_iou": round(float(assoc_meta.iou), 4) if assoc_meta else 0.0,
                    "vehicle_containment": round(float(assoc_meta.containment), 4) if assoc_meta else 0.0,
                    "selected_variant": ensemble.selected_variant,
                    "ocr_passes": [
                        {
                            "variant": pr.variant,
                            "raw_text": pr.raw_text,
                            "normalized": pr.normalized,
                            "confidence": pr.confidence,
                            "matches_pattern": pr.matches_pattern,
                            "elapsed_ms": pr.elapsed_ms,
                        }
                        for pr in ensemble.passes
                    ],
                    "debug_crops": ensemble.debug_crops,
                    "ocr_timing": ocr_timing,
                }
            )
            # Short top-line state+RTO fragments (MH12) → defer remaining Stage-1
            # candidates to primary-ROI two-line search. Never defer on watermark /
            # non-plate short junk (alamy) — those must not skip real plate crops.
            compact_out = strip_plate(result_text)
            is_state_rto_only = bool(re.match(r"^[A-Z]{2}[0-9]{1,2}$", compact_out or ""))
            if (
                adaptive_fast_path
                and not live_mode
                and is_state_rto_only
                and not non_plate
                and not marker_only
                and not (ensemble.matches_pattern and ensemble.ocr_confident)
            ):
                stage_timing["stage1_short_fragment_defer_roi"] = True
                break
            # Adaptive early-exit: ONLY stop on a confident *Indian-pattern* plate
            # on the primary vehicle. Never stop on IU / lights / non-pattern OCR.
            if (
                (live_mode or adaptive_fast_path)
                and ensemble.ocr_confident
                and ensemble.matches_pattern
                and not non_plate
                and not marker_only
            ):
                on_primary = bool(assoc_meta.on_primary) if assoc_meta else True
                remaining_primary = any(
                    (plate_assoc_meta[j].on_primary if j < len(plate_assoc_meta) else False)
                    for j in range(idx + 1, len(plate_dets))
                )
                if live_mode or on_primary or not remaining_primary:
                    break

        stage1_ocr_ms = round(float(stage_timing.get("ocr_ms") or 0.0) - ocr_ms_before, 2)
        stage1_crop_ms = round(float(stage_timing.get("crop_ms") or 0.0) - crop_ms_before, 2)
        print(
            f"[ANPR TIME] 5) Stage-1 preprocess/crop: {stage1_crop_ms:.2f} ms "
            f"({stage1_crop_ms / 1000.0:.4f} s)",
            flush=True,
        )
        print(
            f"[ANPR TIME] 6) Stage-1 OCR text recognition: {stage1_ocr_ms:.2f} ms "
            f"({stage1_ocr_ms / 1000.0:.4f} s)  [plates_ocrd={stage_timing.get('plates_ocrd')}]",
            flush=True,
        )
        _ = t_ocr_stage1  # wall span covered by accumulated ocr_ms / crop_ms

        # Second-stage: primary-vehicle / center-lower motorcycle ROI search.
        # Recovers two-line night plates when stage-1 missed the primary plate
        # (even if a background car plate already matched the Indian pattern).
        has_primary_pattern = any(
            r.get("matches_pattern")
            and r.get("on_primary_vehicle")
            and not r.get("non_plate_text")
            for r in plates_out
        )
        stage_timing["primary_roi_invoked"] = False
        stage_timing["primary_roi_skip_reason"] = None
        if live_mode:
            stage_timing["primary_roi_skip_reason"] = "live_mode"
        elif not adaptive_fast_path:
            stage_timing["primary_roi_skip_reason"] = "adaptive_fast_path_off"
        elif has_primary_pattern:
            stage_timing["primary_roi_skip_reason"] = "primary_already_has_pattern"
        run_primary_roi = (not live_mode) and adaptive_fast_path and (not has_primary_pattern)
        if not run_primary_roi:
            print(
                f"[ANPR TIME] 7-8) Primary-ROI 2nd-stage SKIPPED "
                f"({stage_timing['primary_roi_skip_reason'] or 'n/a'})",
                flush=True,
            )
        if run_primary_roi:
            t_roi = step_timer()
            roi_diag: dict[str, Any] = {}
            roi_dets = detect_plates_in_primary_rois(
                frame,
                vehicles=vehicles,
                vehicle_refs=vehicle_refs,
                primary_idx=primary_idx,
                max_proposals=8,
                diagnostics=roi_diag,
            )
            # Prefer two-line / wider motorcycle plate boxes before thin false crops.
            def _roi_ocr_priority(det: PlateDetection) -> tuple[Any, ...]:
                bw = float(det.bbox.w)
                bh = max(float(det.bbox.h), 1.0)
                asp = bw / bh
                twoline = 1 if 1.35 <= asp <= 3.5 else 0
                wide = 1 if bw >= 160 else 0
                label = str(det.class_name or "")
                centerish = 1 if ("center_" in label or "primary_" in label) else 0
                return (twoline, wide, centerish, bw * bh, float(det.confidence))

            roi_dets = sorted(roi_dets, key=_roi_ocr_priority, reverse=True)
            stage_timing["primary_roi_invoked"] = True
            stage_timing["primary_roi_ms"] = print_step(
                f"7) Primary-ROI 2nd-stage plate detect (proposals={len(roi_dets)})",
                t_roi,
            )
            stage_timing["primary_roi_proposals"] = len(roi_dets)
            stage_timing["primary_roi_diagnostics"] = roi_diag
            stage_timing["ocr_budget_max"] = MAX_PRIMARY_ROI_OCR_CALLS
            logger.info(
                "plate.primary_roi_stage n=%s variants=%s twoline=%s rois=%s",
                len(roi_dets),
                roi_diag.get("variant_count"),
                roi_diag.get("twoline_candidates"),
                roi_diag.get("roi_labels"),
            )
            t_roi_ocr = step_timer()
            ocr_ms_roi_before = float(stage_timing.get("ocr_ms") or 0.0)

            def _roi_ocr_call_count(sess: Any) -> int:
                if sess is None:
                    return 0
                return sum(
                    1
                    for c in sess.calls
                    if str(getattr(c, "stage", "") or "").startswith("primary_roi")
                )

            for p in roi_dets:
                perf_sess = ocr_perf.get_session()
                roi_calls = _roi_ocr_call_count(perf_sess)
                if roi_calls >= MAX_PRIMARY_ROI_OCR_CALLS:
                    stage_timing["early_exit_reason"] = "ocr_budget_exhausted"
                    stage_timing["early_exit_after_call"] = (
                        len(perf_sess.calls) if perf_sess is not None else roi_calls
                    )
                    print(
                        f"[OCR PERF] stop proposals: primary-ROI budget "
                        f"{roi_calls}/{MAX_PRIMARY_ROI_OCR_CALLS}",
                        flush=True,
                    )
                    break
                if p.confidence < self.settings.min_plate_confidence * 0.5:
                    continue
                roi_label = str(p.class_name or "")
                from_primary_band = (
                    "primary_roi:center_" in roi_label
                    or "primary_roi:primary_" in roi_label
                )
                assoc_meta = associate_plate_to_vehicles(
                    p,
                    vehicle_refs,
                    frame_shape=frame_shape,
                    source_vehicle_index=primary_idx if from_primary_band else None,
                )
                # Center/primary motorcycle bands count as primary for Manual ranking.
                # Do NOT promote background-car ROI proposals to primary.
                if from_primary_band:
                    assoc_meta.on_primary = True
                    if primary_idx is not None:
                        assoc_meta.vehicle_index = primary_idx
                    assoc_meta.pre_ocr_score = max(assoc_meta.pre_ocr_score, 4.0)
                x1, y1, x2, y2 = bbox_to_xyxy(p.bbox.x, p.bbox.y, p.bbox.w, p.bbox.h)
                t_crop = step_timer()
                crop, padded_box = crop_with_padding(
                    frame,
                    x1,
                    y1,
                    x2,
                    y2,
                    pad_ratio=max(0.12, float(self.settings.plate_pad_ratio) * 0.7),
                )
                stage_timing["crop_ms"] = round(
                    stage_timing["crop_ms"] + (step_timer() - t_crop) * 1000.0,
                    2,
                )
                if crop is None:
                    continue
                # Skip refine for already-tight two-line motorcycle crops.
                det_w, det_h = max(1, x2 - x1), max(1, y2 - y1)
                det_aspect = det_w / max(det_h, 1)
                ocr_crop = crop
                if not (1.3 <= det_aspect <= 4.0 and det_h <= 160):
                    refined, inner_xyxy, refine_meta = refine_loose_plate_crop(crop)
                    if refined is not None and inner_xyxy is not None:
                        ocr_crop = refined
                        padded_box = offset_bbox(padded_box, inner_xyxy)
                        x1, y1, x2, y2 = padded_box
                debug_dir = None
                if self.settings.ocr_save_debug_crops:
                    debug_dir = self.settings.ocr_debug_dir
                ensemble = self._run_multipass_ocr_profiled(
                    ocr_crop,
                    stage="primary_roi",
                    crop_id=f"roi_{roi_label}",
                    bbox=[x1, y1, x2, y2],
                    source=roi_label,
                    debug_dir=debug_dir,
                    debug_prefix=f"{debug_prefix}_roi",
                    live_mode=False,
                    early_exit_on_confident=True,
                    adaptive_fast_path=True,
                    aggressive_early_exit=True,
                )
                ocr_ms_accum = float((ensemble.timing or {}).get("ocr_total_ms") or 0.0)
                crop_text = ensemble.normalized_text or ensemble.raw_text or ""
                compact_roi = strip_plate(crop_text)
                is_state_rto_partial = bool(
                    re.match(r"^[A-Z]{2}[0-9]{1,2}$", compact_roi or "")
                )
                # Digit-only / series+number bottom line fragments.
                is_bottom_partial = bool(
                    compact_roi
                    and not matches_indian_plate(compact_roi)
                    and not is_non_plate_text(crop_text)
                    and (
                        sum(ch.isdigit() for ch in compact_roi) >= 3
                        or re.match(r"^[A-Z]{1,3}[0-9]{1,4}$", compact_roi)
                    )
                )
                if is_state_rto_partial or (
                    (ensemble.timing or {}).get("early_exit_reason") == "primary_partial_state_rto"
                ):
                    stage_timing["primary_partial_detected"] = compact_roi
                # Partial two-line: expand and retry — top-line MH12 grows DOWN for AB5687.
                if (
                    not ensemble.ocr_confident
                    and strip_plate(crop_text)
                    and not matches_indian_plate(strip_plate(crop_text))
                    and not is_non_plate_text(crop_text)
                ):
                    perf_sess = ocr_perf.get_session()
                    if _roi_ocr_call_count(perf_sess) < MAX_PRIMARY_ROI_OCR_CALLS:
                        if is_state_rto_partial:
                            # Top line only → expand downward to capture second line.
                            stage_timing["two_line_recovery_triggered"] = True
                            expanded, exp_box = expand_partial_plate_crop(
                                frame, x1, y1, x2, y2, grow_up=0.15, grow_y=1.35
                            )
                        elif is_bottom_partial:
                            stage_timing["two_line_recovery_triggered"] = True
                            expanded, exp_box = expand_partial_plate_crop(
                                frame, x1, y1, x2, y2, grow_up=1.35, grow_y=0.25
                            )
                        else:
                            expanded, exp_box = expand_partial_plate_crop(
                                frame, x1, y1, x2, y2, grow_up=1.2, grow_y=0.25
                            )
                        if expanded is not None:
                            exp_ens = self._run_multipass_ocr_profiled(
                                expanded,
                                stage="primary_roi_wide",
                                crop_id=f"roi_wide_{roi_label}",
                                bbox=list(exp_box) if exp_box else [x1, y1, x2, y2],
                                source=f"{roi_label}_wide",
                                debug_dir=debug_dir,
                                debug_prefix=f"{debug_prefix}_roi_wide",
                                live_mode=False,
                                early_exit_on_confident=True,
                                adaptive_fast_path=True,
                                aggressive_early_exit=True,
                            )
                            ocr_ms_accum += float((exp_ens.timing or {}).get("ocr_total_ms") or 0.0)
                            # Stitch first-line fragment with wide/second-line result.
                            stitched = stitch_plate_fragments(
                                crop_text,
                                exp_ens.normalized_text or "",
                                exp_ens.raw_text or "",
                                *(
                                    (pr.raw_text or "")
                                    for pr in (ensemble.passes or [])
                                ),
                                *(
                                    (pr.raw_text or "")
                                    for pr in (exp_ens.passes or [])
                                ),
                            )
                            if stitched and matches_indian_plate(stitched):
                                exp_ens.normalized_text = stitched
                                exp_ens.raw_text = f"{crop_text} {exp_ens.raw_text or ''}".strip()
                                exp_ens.matches_pattern = True
                                exp_ens.ocr_confident = True
                                exp_ens.ocr_confidence = max(
                                    float(exp_ens.ocr_confidence or 0.0),
                                    float(ensemble.ocr_confidence or 0.0),
                                    0.85,
                                )
                                stage_timing["two_line_recovery_result"] = stitched
                                ensemble = exp_ens
                                padded_box = exp_box
                                x1, y1, x2, y2 = exp_box
                            elif exp_ens.ocr_confident or (
                                exp_ens.matches_pattern
                                and not is_non_plate_text(
                                    exp_ens.normalized_text or exp_ens.raw_text or ""
                                )
                            ):
                                ensemble = exp_ens
                                padded_box = exp_box
                                x1, y1, x2, y2 = exp_box
                stage_timing["ocr_ms"] = round(stage_timing["ocr_ms"] + ocr_ms_accum, 2)
                stage_timing["plates_ocrd"] = int(stage_timing["plates_ocrd"]) + 1
                result_text = ensemble.normalized_text or ensemble.raw_text or ""
                non_plate = is_non_plate_text(result_text)
                marker_only = is_plate_marker_noise(result_text)
                if ensemble.matches_pattern and ensemble.ocr_confident and not non_plate:
                    combined = float(p.confidence) * 0.35 + ensemble.ocr_confidence * 0.65
                elif ensemble.matches_pattern and not non_plate:
                    combined = float(p.confidence) * 0.30 + ensemble.ocr_confidence * 0.40
                else:
                    combined = float(p.confidence) * 0.15
                plates_out.append(
                    {
                        "raw_text": ensemble.raw_text,
                        "normalized_text": None if non_plate else ensemble.normalized_text,
                        "normalized_plate": None if non_plate else ensemble.normalized_text,
                        "ocr_confident": bool(ensemble.ocr_confident and not non_plate),
                        "confidence": round(combined, 4),
                        "ocr_confidence": round(ensemble.ocr_confidence, 4),
                        "plate_confidence": round(float(p.confidence), 4),
                        "matches_pattern": bool(ensemble.matches_pattern and not non_plate),
                        "marker_noise": marker_only,
                        "non_plate_text": non_plate,
                        "bbox": [x1, y1, x2, y2],
                        "padded_bbox": list(padded_box),
                        "vehicle_index": assoc_meta.vehicle_index,
                        "track_id": (
                            vehicle_refs[assoc_meta.vehicle_index].track_id
                            if assoc_meta.vehicle_index is not None
                            and 0 <= assoc_meta.vehicle_index < len(vehicle_refs)
                            else (
                                vehicle_refs[primary_idx].track_id
                                if from_primary_band
                                and primary_idx is not None
                                and primary_idx < len(vehicle_refs)
                                else None
                            )
                        ),
                        "on_primary_vehicle": bool(assoc_meta.on_primary),
                        "vehicle_iou": round(float(assoc_meta.iou), 4),
                        "vehicle_containment": round(float(assoc_meta.containment), 4),
                        "primary_roi_stage": True,
                        "selected_variant": ensemble.selected_variant,
                        "ocr_passes": [
                            {
                                "variant": pr.variant,
                                "raw_text": pr.raw_text,
                                "normalized": pr.normalized,
                                "confidence": pr.confidence,
                                "matches_pattern": pr.matches_pattern,
                                "elapsed_ms": pr.elapsed_ms,
                            }
                            for pr in ensemble.passes
                        ],
                        "debug_crops": ensemble.debug_crops,
                        "ocr_timing": ensemble.timing or {},
                    }
                )
                # Stop all remaining proposals once a full primary Indian plate is known.
                if (
                    ensemble.ocr_confident
                    and ensemble.matches_pattern
                    and not non_plate
                    and (from_primary_band or assoc_meta.on_primary)
                ):
                    stage_timing["primary_roi_hit"] = True
                    stage_timing["early_exit_reason"] = (
                        (ensemble.timing or {}).get("early_exit_reason")
                        or "primary_roi_full_plate"
                    )
                    perf_sess = ocr_perf.get_session()
                    stage_timing["early_exit_after_call"] = (
                        len(perf_sess.calls) if perf_sess is not None else None
                    )
                    print(
                        f"[OCR PERF] early_exit_reason={stage_timing['early_exit_reason']} "
                        f"after_call={stage_timing['early_exit_after_call']}",
                        flush=True,
                    )
                    break

            roi_ocr_ms = round(float(stage_timing.get("ocr_ms") or 0.0) - ocr_ms_roi_before, 2)
            print(
                f"[ANPR TIME] 8) Primary-ROI OCR text recognition: {roi_ocr_ms:.2f} ms "
                f"({roi_ocr_ms / 1000.0:.4f} s)",
                flush=True,
            )
            _ = t_roi_ocr

        # Secondary / background OCR only if primary still has no full Indian plate.
        # Uses a separate small budget so it cannot starve primary recovery above.
        has_primary_full = any(
            r.get("matches_pattern")
            and r.get("on_primary_vehicle")
            and not r.get("non_plate_text")
            for r in plates_out
        )
        if (
            adaptive_fast_path
            and not live_mode
            and not has_primary_full
            and _secondary_pairs_held
        ):
            stage_timing["secondary_ocr_invoked"] = True
            sec_budget = 0
            for det, meta in _secondary_pairs_held[:3]:
                if sec_budget >= MAX_SECONDARY_OCR_CALLS:
                    break
                x1, y1, x2, y2 = bbox_to_xyxy(det.bbox.x, det.bbox.y, det.bbox.w, det.bbox.h)
                crop, padded_box = crop_with_padding(
                    frame, x1, y1, x2, y2, pad_ratio=self.settings.plate_pad_ratio
                )
                if crop is None:
                    continue
                before = len(ocr_perf.get_session().calls) if ocr_perf.get_session() else 0
                ensemble = self._run_multipass_ocr_profiled(
                    crop,
                    stage="stage1_secondary",
                    crop_id=f"sec_{sec_budget}",
                    bbox=[x1, y1, x2, y2],
                    source="secondary",
                    live_mode=False,
                    early_exit_on_confident=True,
                    adaptive_fast_path=True,
                    aggressive_early_exit=False,
                )
                after = len(ocr_perf.get_session().calls) if ocr_perf.get_session() else before
                sec_budget += max(0, after - before)
                stage_timing["ocr_ms"] = round(
                    stage_timing["ocr_ms"]
                    + float((ensemble.timing or {}).get("ocr_total_ms") or 0.0),
                    2,
                )
                result_text = ensemble.normalized_text or ensemble.raw_text or ""
                non_plate = is_non_plate_text(result_text)
                plates_out.append(
                    {
                        "raw_text": ensemble.raw_text,
                        "normalized_text": None if non_plate else ensemble.normalized_text,
                        "normalized_plate": None if non_plate else ensemble.normalized_text,
                        "ocr_confident": bool(ensemble.ocr_confident and not non_plate),
                        "confidence": round(float(ensemble.ocr_confidence or 0.0) * 0.5, 4),
                        "ocr_confidence": round(float(ensemble.ocr_confidence or 0.0), 4),
                        "plate_confidence": round(float(det.confidence), 4),
                        "matches_pattern": bool(ensemble.matches_pattern and not non_plate),
                        "marker_noise": is_plate_marker_noise(result_text),
                        "non_plate_text": non_plate,
                        "bbox": [x1, y1, x2, y2],
                        "padded_bbox": list(padded_box) if padded_box else [x1, y1, x2, y2],
                        "vehicle_index": meta.vehicle_index if meta else None,
                        "track_id": (
                            vehicle_refs[meta.vehicle_index].track_id
                            if meta
                            and meta.vehicle_index is not None
                            and 0 <= meta.vehicle_index < len(vehicle_refs)
                            else None
                        ),
                        "on_primary_vehicle": bool(meta.on_primary) if meta else False,
                        "vehicle_iou": round(float(meta.iou), 4) if meta else 0.0,
                        "vehicle_containment": round(float(meta.containment), 4) if meta else 0.0,
                        "secondary_stage": True,
                        "selected_variant": ensemble.selected_variant,
                        "ocr_passes": [],
                        "debug_crops": [],
                        "ocr_timing": ensemble.timing or {},
                    }
                )
                if ensemble.matches_pattern and ensemble.ocr_confident and not non_plate:
                    break

        # Stitch OCR fragments into a full Indian plate.
        # Prefer primary-vehicle / two-line motorcycle fragments (MH12 + AB5687)
        # even when a background car plate already matched the pattern.
        def _collect_stitch_fragments(*, primary_only: bool) -> list[tuple[float, str]]:
            fragments: list[tuple[float, str]] = []
            seen_frag: set[str] = set()
            for row in plates_out:
                if primary_only and not row.get("on_primary_vehicle"):
                    continue
                if row.get("non_plate_text") and not (row.get("ocr_passes") or []):
                    continue
                bbox = row.get("bbox") or [0, 0, 0, 0]
                primary_boost = 0.0 if row.get("on_primary_vehicle") else 1000.0
                sort_key = primary_boost + float(bbox[0]) + float(bbox[1]) * 0.001
                candidates = [row.get("raw_text") or ""]
                for pr in row.get("ocr_passes") or []:
                    candidates.append(pr.get("raw_text") or "")
                    candidates.append(pr.get("normalized") or "")
                for text in candidates:
                    compact = strip_plate(text)
                    if not compact or compact in seen_frag:
                        continue
                    # Complete plates are ranked on their own; stitch only incomplete
                    # two-line pieces (MH12 + AB5687) so a background MH14 does not
                    # short-circuit stitch_plate_fragments.
                    if matches_indian_plate(compact):
                        continue
                    # Allow short stitchable pieces; skip watermarks / IU as finals.
                    if is_non_plate_text(compact):
                        if not (len(compact) <= 2 and (compact.isdigit() or compact.isalpha())):
                            continue
                        if is_plate_marker_noise(compact):
                            continue
                    seen_frag.add(compact)
                    fragments.append((sort_key, compact))
            fragments.sort(key=lambda item: (item[0], -len(item[1])))
            return fragments

        def _apply_stitch(fragments: list[tuple[float, str]], stitched: str) -> None:
            host = None
            for row in plates_out:
                if row.get("non_plate_text"):
                    continue
                if row.get("on_primary_vehicle") and row.get("raw_text"):
                    # Prefer a compact two-line host box over a wide car plate.
                    bb = row.get("bbox") or [0, 0, 1, 1]
                    aw = max(float(bb[2]) - float(bb[0]), 1.0)
                    ah = max(float(bb[3]) - float(bb[1]), 1.0)
                    if 1.3 <= (aw / ah) <= 3.5 or host is None:
                        host = row
                        if 1.3 <= (aw / ah) <= 3.5:
                            break
            if host is None:
                for row in plates_out:
                    if row.get("non_plate_text"):
                        continue
                    if row.get("raw_text"):
                        host = row
                        break
            if host is None and plates_out:
                host = plates_out[0]
            if host is None:
                return
            host["raw_text"] = " ".join(dict.fromkeys(t for _, t in fragments))
            host["normalized_text"] = stitched
            host["normalized_plate"] = stitched
            host["matches_pattern"] = True
            host["ocr_confident"] = True
            host["non_plate_text"] = False
            host["marker_noise"] = False
            host["stitched_from_fragments"] = True
            host["on_primary_vehicle"] = True
            host["confidence"] = max(float(host.get("confidence") or 0.0), 0.88)
            host["ocr_confidence"] = max(float(host.get("ocr_confidence") or 0.0), 0.88)

        primary_frags = _collect_stitch_fragments(primary_only=True)
        stitched_primary = stitch_plate_fragments(*(t for _, t in primary_frags)) if primary_frags else None
        stage_timing["stitch_primary_fragments"] = [t for _, t in primary_frags][:12]
        stage_timing["stitch_primary_result"] = stitched_primary
        if stitched_primary and matches_indian_plate(stitched_primary):
            _apply_stitch(primary_frags, stitched_primary)
            stage_timing["stitched_applied"] = stitched_primary
        elif not any(r.get("matches_pattern") for r in plates_out):
            all_frags = _collect_stitch_fragments(primary_only=False)
            stitched_all = stitch_plate_fragments(*(t for _, t in all_frags)) if all_frags else None
            stage_timing["stitch_all_result"] = stitched_all
            if stitched_all and matches_indian_plate(stitched_all):
                _apply_stitch(all_frags, stitched_all)
                stage_timing["stitched_applied"] = stitched_all

        t_post = step_timer()
        # Rank: pattern > primary vehicle > OCR conf > geometry.
        plates_out.sort(key=final_plate_rank_key, reverse=True)
        stage_timing["postprocess_ms"] = print_step("9) Postprocess / stitch / rank", t_post)
        reliable = [p for p in plates_out if p.get("matches_pattern") and not p.get("non_plate_text")]
        if reliable:
            top = reliable[0]
            stage_timing["selected_bbox"] = list(top.get("bbox") or [])
            stage_timing["selected_plate_vehicle"] = {
                "vehicle_index": top.get("vehicle_index"),
                "track_id": top.get("track_id"),
                "on_primary": top.get("on_primary_vehicle"),
            }
            stage_timing["selected_ocr"] = {
                "normalized": top.get("normalized_text"),
                "raw": top.get("raw_text"),
                "ocr_confidence": top.get("ocr_confidence"),
                "matches_pattern": True,
            }
        else:
            stage_timing["selected_ocr"] = None
            stage_timing["no_reliable_plate"] = True

        # Per-vehicle results (live + Manual diagnostics). Manual still ranks
        # primary first via final_plate_rank_key / best_plate.
        vehicle_results: list[dict[str, Any]] = []
        for vrow in vehicles_out:
            tid = vrow.get("track_id")
            vi = vrow.get("index")
            owned = [
                p
                for p in plates_out
                if (tid and p.get("track_id") == tid)
                or (vi is not None and p.get("vehicle_index") == vi)
            ]
            owned_reliable = [
                p for p in owned if p.get("matches_pattern") and not p.get("non_plate_text")
            ]
            best_v = owned_reliable[0] if owned_reliable else (owned[0] if owned else None)
            vehicle_results.append(
                {
                    "track_id": tid,
                    "vehicle_index": vi,
                    "is_primary": bool(vrow.get("is_primary")),
                    "bbox": list(vrow.get("bbox") or []),
                    "confidence": vrow.get("confidence"),
                    "best_plate": (best_v or {}).get("normalized_text") if best_v else None,
                    "best_raw": (best_v or {}).get("raw_text") if best_v else None,
                    "matches_pattern": bool((best_v or {}).get("matches_pattern")) if best_v else False,
                    "ocr_confidence": (best_v or {}).get("ocr_confidence") if best_v else None,
                    "plate_count": len(owned),
                }
            )
        stage_timing["vehicle_results"] = vehicle_results
        if self.vehicle_tracker is not None and live_mode:
            try:
                stage_timing["active_tracks"] = self.vehicle_tracker.snapshot()
            except Exception:  # noqa: BLE001
                stage_timing["active_tracks"] = []

        return {
            "vehicles": vehicles_out,
            "vehicle_results": vehicle_results,
            "plates": plates_out,
            "vehicle_detected": bool(vehicles_out),
            # Only true when at least one Indian-pattern plate was recovered.
            "plate_detected": bool(reliable),
            "error": None if reliable else ("no reliable plate detected" if plates_out else None),
            "timing": stage_timing,
        }
