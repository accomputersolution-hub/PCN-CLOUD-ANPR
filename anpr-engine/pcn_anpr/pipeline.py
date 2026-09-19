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
from pcn_anpr.normalize import (
    extract_all_indian_plates,
    is_non_plate_text,
    is_plate_marker_noise,
    matches_indian_plate,
    sanitize_plate_text,
    stitch_plate_fragments,
    strip_plate,
)
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
from pcn_anpr.crop_refine import is_loose_vehicle_front_crop, offset_bbox, refine_loose_plate_crop
from pcn_anpr.preprocess import crop_with_padding
from pcn_anpr.vehicle_assoc import (
    associate_plate_to_vehicles,
    filter_vehicle_detections,
    final_plate_rank_key,
    select_primary_vehicle,
    synthesize_vehicles_from_plates,
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


def _host_vehicle_clamp_xyxy(
    assoc_meta: Any,
    vehicle_refs: list[Any],
    *,
    fw: int,
    fh: int,
    pad_frac: float = 0.02,
) -> tuple[float, float, float, float] | None:
    """Host vehicle ROI + small pad — used to stop OCR expand / wide crops exploding."""
    if assoc_meta is None:
        return None
    vi = getattr(assoc_meta, "vehicle_index", None)
    if vi is None or not (0 <= int(vi) < len(vehicle_refs)):
        return None
    vb = vehicle_refs[int(vi)].bbox_xyxy
    vx1, vy1, vx2, vy2 = float(vb[0]), float(vb[1]), float(vb[2]), float(vb[3])
    pad = float(fw) * pad_frac
    return (
        max(0.0, vx1 - pad),
        max(0.0, vy1 - pad),
        min(float(fw), vx2 + pad),
        min(float(fh), vy2 + pad),
    )


def _should_clamp_plate_expand(class_name: str | None) -> bool:
    cls = str(class_name or "")
    return cls in {"opencv_fallback_wide", "opencv_fallback", "yolo_quality_reject_ocr_fallback"}


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

    def _save_association_debug(
        self,
        frame: Any,
        *,
        vehicles_out: list[dict[str, Any]],
        associated: list[Any],
        plate_dets_after: list[Any] | None = None,
    ) -> None:
        """Write vehicle crops, accepted plate crops, and association JSON for Manual debug."""
        import json

        try:
            import cv2
        except ImportError:
            return
        out_root = Path(self.settings.output_dir) / "assoc_debug"
        veh_dir = out_root / "vehicles"
        plate_dir = out_root / "plates"
        veh_dir.mkdir(parents=True, exist_ok=True)
        plate_dir.mkdir(parents=True, exist_ok=True)
        fh, fw = int(frame.shape[0]), int(frame.shape[1])
        assoc_rows: list[dict[str, Any]] = []
        for v in vehicles_out:
            bbox = list(v.get("bbox") or [])
            if len(bbox) < 4:
                continue
            x1, y1, x2, y2 = [int(v) for v in bbox[:4]]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(fw, x2), min(fh, y2)
            if x2 > x1 and y2 > y1:
                crop = frame[y1:y2, x1:x2]
                name = f"vehicle_{v.get('index')}_{v.get('label', 'veh')}.jpg"
                cv2.imwrite(str(veh_dir / name), crop)
        for i, assoc in enumerate(associated):
            det = assoc.detection
            x1 = int(det.bbox.x)
            y1 = int(det.bbox.y)
            x2 = int(det.bbox.x + det.bbox.w)
            y2 = int(det.bbox.y + det.bbox.h)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(fw, x2), min(fh, y2)
            if x2 > x1 and y2 > y1:
                crop = frame[y1:y2, x1:x2]
                cv2.imwrite(str(plate_dir / f"plate_{i}_v{assoc.vehicle_index}.jpg"), crop)
            assoc_rows.append(
                {
                    "plate_index": i,
                    "bbox_xyxy": [x1, y1, x2, y2],
                    "assigned_vehicle": assoc.vehicle_index,
                    "assignment_method": getattr(assoc, "assignment_method", None),
                    "on_primary": bool(assoc.on_primary),
                    "containment": round(float(assoc.containment), 4),
                    "iou": round(float(assoc.iou), 4),
                    "confidence": round(float(det.confidence), 4),
                }
            )
        payload = {
            "plate_detector": getattr(self.settings, "plate_detector", None),
            "vehicle_detector": getattr(self.settings, "vehicle_detector", None),
            "vehicles": vehicles_out,
            "plate_associations": assoc_rows,
        }
        (out_root / "associations.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        print(
            f"[ANPR DEBUG] assoc dumps -> {out_root} "
            f"vehicles={len(vehicles_out)} plates={len(assoc_rows)}",
            flush=True,
        )

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
        strong_early_exit: bool = False,
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
                strong_early_exit=strong_early_exit,
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

    def process_image(
        self,
        image_path: str | Path,
        *,
        anpr_roi: dict[str, Any] | None = None,
        camera_id: str | None = None,
    ) -> dict[str, Any]:
        """Process a saved JPEG/PNG and return structured ANPR JSON (no DB events).

        Optional ``anpr_roi`` is a normalized camera gate zone
        ``{enabled,x,y,w,h}`` (0..1). When omitted/disabled, behavior is unchanged.
        """
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
                result = self._infer(
                    frame,
                    debug_prefix=path.stem,
                    adaptive_fast_path=True,
                    anpr_roi=anpr_roi,
                    camera_id=camera_id,
                )
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
        anpr_roi: dict[str, Any] | None = None,
        camera_id: str | None = None,
    ) -> dict[str, Any]:
        from pcn_anpr.anpr_roi import filter_vehicles_by_anpr_roi, normalize_roi

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
            "camera_id": camera_id,
            "roi_enabled": False,
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
        detector_name = getattr(self.vehicle_detector, "detector_name", None) or type(
            self.vehicle_detector
        ).__name__
        stage_timing["vehicle_detector"] = detector_name
        t0 = step_timer()
        try:
            vehicles = self.vehicle_detector.detect(frame) or []
        except Exception:
            vehicles = []
        stage_timing["vehicle_ms"] = print_step("3) Vehicle detection", t0)

        fh = fw = 0
        if hasattr(frame, "shape") and len(frame.shape) >= 2:
            fh, fw = int(frame.shape[0]), int(frame.shape[1])
        frame_shape_early = (fh, fw) if fh and fw else (1, 1)
        detector_raw_n = len(vehicles)
        vehicles = filter_vehicle_detections(vehicles, frame_shape=frame_shape_early)
        stage_timing["vehicle_detector_raw"] = detector_raw_n
        stage_timing["vehicle_detector_kept"] = len(vehicles)
        # Optional camera ANPR zone: YOLO still runs full-frame; only ROI vehicles
        # enter plate/OCR (no wide-fallback / OCR for ignored hosts).
        vehicles, roi_stats = filter_vehicles_by_anpr_roi(
            vehicles,
            anpr_roi=anpr_roi,
            frame_shape=frame_shape_early,
        )
        stage_timing.update(
            {
                "roi_enabled": roi_stats.get("roi_enabled"),
                "roi_norm": roi_stats.get("roi_norm"),
                "roi_bbox": roi_stats.get("roi_bbox_xyxy"),
                "total_yolo_vehicles": roi_stats.get("total_yolo_vehicles"),
                "roi_vehicles": roi_stats.get("roi_vehicles"),
                "ignored_outside_roi": roi_stats.get("ignored_outside_roi"),
            }
        )
        print(
            f"[ANPR ROI] camera_id={camera_id} roi_enabled={roi_stats.get('roi_enabled')} "
            f"roi_bbox={roi_stats.get('roi_bbox_xyxy')} "
            f"total_yolo_vehicles={roi_stats.get('total_yolo_vehicles')} "
            f"roi_vehicles={roi_stats.get('roi_vehicles')} "
            f"ignored_outside_roi={roi_stats.get('ignored_outside_roi')}",
            flush=True,
        )
        prefer_detector_hosts = bool(
            getattr(self.vehicle_detector, "prefer_detector_hosts", False)
        ) or str(detector_name).lower() in {"yolo", "yolovehicledetector"}
        stage_timing["prefer_detector_hosts"] = prefer_detector_hosts
        print(
            f"[ANPR MULTI] detector={detector_name} vehicle_count={len(vehicles)}",
            flush=True,
        )
        for i, v in enumerate(vehicles):
            print(
                f"[ANPR MULTI] vehicle[{i}] class={v.label} conf={float(v.confidence):.3f} "
                f"bbox=[{int(v.bbox.x)},{int(v.bbox.y)},"
                f"{int(v.bbox.x + v.bbox.w)},{int(v.bbox.y + v.bbox.h)}]",
                flush=True,
            )

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
            area_ratio = 0.0
            if fh and fw:
                area_ratio = float((x2 - x1) * (y2 - y1)) / float(max(1, fh * fw))
            src = (
                "synthetic"
                if str(getattr(v, "label", "")).startswith("vehicle_from_plate")
                else "detector"
            )
            vehicles_out.append(
                {
                    "index": i,
                    "track_id": v.track_id,
                    "label": v.label,
                    "confidence": float(v.confidence),
                    "bbox": [x1, y1, x2, y2],
                    "is_primary": False,
                    "source": src,
                    "area_ratio": round(area_ratio, 4),
                }
            )
            print(
                f"[ANPR MULTI] vehicle[{i}] bbox={[x1, y1, x2, y2]} "
                f"source={src} area_ratio={area_ratio:.3f}",
                flush=True,
            )

        plate_dets: list[PlateDetection] = []
        plate_sources: list[int | None] = []
        t0 = step_timer()
        try:
            if hasattr(self.plate_detector, "begin_frame"):
                try:
                    self.plate_detector.begin_frame()
                except Exception:  # noqa: BLE001
                    pass
            if vehicles:
                for vi, v in enumerate(vehicles):
                    # Tag index for hybrid per-vehicle logs (vehicle_id).
                    try:
                        setattr(v, "vehicle_id", vi)
                    except Exception:  # noqa: BLE001
                        pass
                    found = self.plate_detector.detect(frame, v) or []
                    for p in found:
                        plate_dets.append(p)
                        plate_sources.append(vi)
            # Manual: also scan full frame unless hybrid already covered each vehicle
            # (hybrid.skip_full_frame_scan avoids redundant OpenCV full-frame).
            # When camera ANPR ROI is enabled, never run unbounded full-frame plate scan.
            skip_full = bool(getattr(self.plate_detector, "skip_full_frame_scan", False))
            roi_active = bool(normalize_roi(anpr_roi))
            if roi_active:
                skip_full = True
            if not live_mode and not skip_full:
                full_found = self.plate_detector.detect(frame, None) or []
                for p in full_found:
                    plate_dets.append(p)
                    plate_sources.append(None)
            elif not live_mode and skip_full:
                stage_timing["plate_full_frame_skipped"] = True
                reason = "anpr_roi" if roi_active else "hybrid per-vehicle"
                print(f"[ANPR MULTI] plate full-frame scan skipped ({reason})", flush=True)
            elif not plate_dets:
                plate_dets = self.plate_detector.detect(frame, None) or []
                plate_sources = [None] * len(plate_dets)
            # Deduplicate near-identical plate boxes (per-vehicle + full-frame overlap).
            if len(plate_dets) > 1:
                keep_d: list[PlateDetection] = []
                keep_s: list[int | None] = []
                for p, src in zip(plate_dets, plate_sources):
                    dup = False
                    for k in keep_d:
                        ax1, ay1 = p.bbox.x, p.bbox.y
                        ax2, ay2 = p.bbox.x + p.bbox.w, p.bbox.y + p.bbox.h
                        bx1, by1 = k.bbox.x, k.bbox.y
                        bx2, by2 = k.bbox.x + k.bbox.w, k.bbox.y + k.bbox.h
                        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
                        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
                        inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
                        union = p.bbox.w * p.bbox.h + k.bbox.w * k.bbox.h - inter
                        if union > 0 and inter / union > 0.55:
                            dup = True
                            break
                    if not dup:
                        keep_d.append(p)
                        keep_s.append(src)
                plate_dets, plate_sources = keep_d, keep_s
        except Exception:
            plate_dets = []
            plate_sources = []
        stage_timing["plate_ms"] = print_step(
            f"4) Plate detection {getattr(self.settings, 'plate_detector', 'opencv')} "
            f"(candidates={len(plate_dets)})",
            t0,
        )

        configured_mode = getattr(self.settings, "plate_detector", "opencv") or "opencv"
        stage_timing["plate_detector_mode"] = configured_mode
        stage_timing["plate_detector_used"] = configured_mode
        # Compare / AI-fallback / hybrid modes may expose last_meta for evaluation timing.
        meta = getattr(self.plate_detector, "last_meta", None)
        if isinstance(meta, dict) and meta:
            stage_timing["plate_detector_mode"] = meta.get("mode") or configured_mode
            stage_timing["plate_detector_used"] = meta.get("used") or configured_mode
            stage_timing["plate_compare"] = {
                "used": meta.get("used"),
                "opencv_time_ms": meta.get("opencv_time_ms"),
                "ai_time_ms": meta.get("ai_time_ms"),
                "yolo_time_ms": meta.get("yolo_time_ms"),
                "opencv_count": meta.get("opencv_count"),
                "ai_count": meta.get("ai_count"),
                "yolo_count": meta.get("yolo_count"),
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
        hybrid_logs = getattr(self.plate_detector, "last_vehicle_logs", None)
        if isinstance(hybrid_logs, list) and hybrid_logs:
            stage_timing["hybrid_vehicle_logs"] = hybrid_logs
            print(
                f"[ANPR HYBRID] frame summary vehicles_logged={len(hybrid_logs)} "
                f"fallbacks={sum(1 for h in hybrid_logs if h.get('fallback_used'))}",
                flush=True,
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

        # Detector-first; synthesize ONLY for plates not hosted by a real vehicle.
        if not live_mode and plate_dets and fh and fw:
            tight_plates = [
                p
                for p in plate_dets
                if 1.6 <= (float(p.bbox.w) / max(float(p.bbox.h), 1.0)) <= 7.5
                and (float(p.bbox.w) * float(p.bbox.h)) <= (fh * fw * 0.05)
                # Skip OSD / timestamp band plates when synthesizing hosts.
                and not (
                    float(p.bbox.y) < fh * 0.10 and float(p.bbox.x) < fw * 0.40
                )
            ]
            vehicles, synth_meta = synthesize_vehicles_from_plates(
                tight_plates,
                frame_shape=frame_shape,
                existing=vehicles,
                max_new=8,
                prefer_detector_hosts=prefer_detector_hosts,
            )
            vehicles = assign_frame_local_track_ids(vehicles)
            vehicles_out = []
            for i, v in enumerate(vehicles):
                x1, y1, x2, y2 = bbox_to_xyxy(v.bbox.x, v.bbox.y, v.bbox.w, v.bbox.h)
                area_ratio = float((x2 - x1) * (y2 - y1)) / float(max(1, fh * fw))
                src = (
                    "synthetic"
                    if str(getattr(v, "label", "")).startswith("vehicle_from_plate")
                    else "detector"
                )
                vehicles_out.append(
                    {
                        "index": i,
                        "track_id": v.track_id,
                        "label": v.label,
                        "confidence": float(v.confidence),
                        "bbox": [x1, y1, x2, y2],
                        "is_primary": False,
                        "source": src,
                        "area_ratio": round(area_ratio, 4),
                    }
                )
                print(
                    f"[ANPR MULTI] vehicle[{i}] bbox={[x1, y1, x2, y2]} "
                    f"source={src} area_ratio={area_ratio:.3f}",
                    flush=True,
                )
            stage_timing["vehicles_synthesized_from_plates"] = bool(
                synth_meta.get("synthesized")
            )
            stage_timing["vehicle_synth_meta"] = synth_meta
            stage_timing["vehicle_count_after_synth"] = len(vehicles)
            print(
                f"[ANPR MULTI] vehicle_merge detector_kept={synth_meta.get('detector_kept')} "
                f"synthesized={synth_meta.get('synthesized')} "
                f"final={synth_meta.get('final_count')} "
                f"from_tight_plates={len(tight_plates)} "
                f"needing_host={synth_meta.get('plates_needing_host')}",
                flush=True,
            )

        vehicle_refs, primary_idx = select_primary_vehicle(
            vehicles, frame_shape=frame_shape, plates=plate_dets
        )
        # Refresh tracker primary flag after prominence selection.
        if self.vehicle_tracker is not None and live_mode and vehicles:
            try:
                vehicles = self.vehicle_tracker.update(vehicles, primary_index=primary_idx)
                vehicle_refs, primary_idx = select_primary_vehicle(
                    vehicles, frame_shape=frame_shape, plates=plate_dets
                )
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
            # CCTV OSD / timestamp strip (top-left) is not a plate — skip early.
            if fh > 0 and fw > 0 and float(y1) < fh * 0.10 and float(x1) < fw * 0.40:
                candidate_report.append(
                    {
                        "bbox": [int(x1), int(y1), int(x2), int(y2)],
                        "confidence": round(float(p.confidence), 4),
                        "quality": {"reject": True, "reasons": ["osd_timestamp_zone"]},
                        "vehicle_index": None,
                        "on_primary_vehicle": False,
                        "pre_ocr_score": 0.0,
                    }
                )
                continue
            # Reject multi-car union boxes — they assign many plates to one host
            # and create oversized synthetic vehicles. Keep large but plate-like
            # single-plate unions (stock photos where the plate fills ~10% of frame).
            plate_area_ratio = 0.0
            plate_aspect = 0.0
            if fh and fw:
                pw = float(max(0, x2 - x1))
                ph = float(max(0, y2 - y1))
                plate_area_ratio = (pw * ph) / float(max(1, fh * fw))
                plate_aspect = pw / max(ph, 1.0)
            plate_like_union = (
                1.8 <= plate_aspect <= 6.5
                and (fh <= 0 or (y2 - y1) <= fh * 0.28)
                and plate_area_ratio <= 0.12
            )
            if plate_area_ratio > 0.12 or (plate_area_ratio > 0.07 and not plate_like_union):
                candidate_report.append(
                    {
                        "bbox": [int(x1), int(y1), int(x2), int(y2)],
                        "confidence": round(float(p.confidence), 4),
                        "quality": {
                            "reject": True,
                            "reasons": [f"oversized_plate_union:{plate_area_ratio:.3f}"],
                        },
                        "vehicle_index": None,
                        "on_primary_vehicle": False,
                        "pre_ocr_score": 0.0,
                    }
                )
                print(
                    f"[ANPR MULTI] plate[rejected] bbox={[int(x1), int(y1), int(x2), int(y2)]} "
                    f"reason=oversized_plate_union area_ratio={plate_area_ratio:.3f}",
                    flush=True,
                )
                continue
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
            plate_cls = str(getattr(p, "class_name", "") or "")
            # Soft-rejected YOLO crops retained by hybrid for OCR — do not drop
            # again on few_char_blobs / chaotic_edges before recognition.
            allow_ocr_despite_quality = plate_cls == "yolo_quality_reject_ocr_fallback"
            if assessment.reject and not allow_ocr_despite_quality:
                logger.info(
                    "plate.candidate_rejected bbox=%s conf=%.3f reasons=%s",
                    row["bbox"],
                    p.confidence,
                    assessment.reasons,
                )
                continue
            if allow_ocr_despite_quality and assessment.reject:
                logger.info(
                    "plate.yolo_quality_reject_ocr_fallback bbox=%s conf=%.3f "
                    "reasons=%s (OCR allowed)",
                    row["bbox"],
                    p.confidence,
                    assessment.reasons,
                )
            blended = float(min(0.95, 0.55 * float(p.confidence) + 0.45 * assessment.score))
            # Boost primary-vehicle plates so they OCR first (adaptive early-exit).
            if assoc.on_primary:
                blended = min(0.95, blended + 0.08)
            # Prefer OCR of retained YOLO crop ahead of OpenCV fallbacks.
            if allow_ocr_despite_quality:
                blended = min(0.95, blended + 0.12)
            det = PlateDetection(
                bbox=BoundingBox(p.bbox.x, p.bbox.y, p.bbox.w, p.bbox.h, blended),
                confidence=blended,
                class_name=plate_cls or "license_plate",
                timing_ms=getattr(p, "timing_ms", None),
            )
            assoc.detection = det
            re_assoc = associate_plate_to_vehicles(
                det,
                vehicle_refs,
                frame_shape=frame_shape,
                source_vehicle_index=assoc.vehicle_index,
            )
            assoc.pre_ocr_score = re_assoc.pre_ocr_score
            assoc.vehicle_index = re_assoc.vehicle_index
            assoc.iou = re_assoc.iou
            assoc.containment = re_assoc.containment
            assoc.on_primary = re_assoc.on_primary
            assoc.assignment_method = re_assoc.assignment_method
            associated.append(assoc)
            print(
                f"[ANPR MULTI] plate[{len(associated)-1}] bbox={[int(x1), int(y1), int(x2), int(y2)]} "
                f"assigned_vehicle={assoc.vehicle_index} "
                f"assignment_method={assoc.assignment_method} "
                f"contain={assoc.containment:.3f} iou={assoc.iou:.3f} "
                f"on_primary={assoc.on_primary}",
                flush=True,
            )

        # Debug dumps: vehicle crops + accepted plate crops + associations (Manual).
        if not live_mode and fh and fw:
            try:
                self._save_association_debug(
                    frame,
                    vehicles_out=vehicles_out,
                    associated=associated if associated else [],
                    plate_dets_after=plate_dets if associated else [],
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("anpr.debug_dump_failed error=%s", exc)

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
            # Stage-1: ONLY primary-vehicle plate crops (prevents Tiago/background
            # plates from entering primary HSRP/widen path). All other vehicles
            # are OCR'd later in secondary with per-vehicle early-exit.
            stage1_pairs: list[tuple[Any, Any]] = []
            secondary_pairs: list[tuple[Any, Any]] = []
            seen_keys: set[Any] = set()

            def _cluster_key(det: Any, meta: Any) -> Any:
                if meta is not None and meta.vehicle_index is not None:
                    return ("v", meta.vehicle_index)
                return ("g", int(det.bbox.x) // 40, int(det.bbox.y) // 40)

            ordered = list(zip(plate_dets, plate_assoc_meta))
            ordered.sort(
                key=lambda item: (
                    0 if (item[1] is not None and item[1].on_primary) else 1,
                    -float(item[0].confidence),
                )
            )
            for det, meta in ordered:
                on_pri = bool(meta is not None and meta.on_primary)
                if on_pri and len(stage1_pairs) < 2:
                    # Allow up to 2 crops on the primary vehicle — a high-conf
                    # empty bumper box must not block the real plate crop.
                    geom = ("g", int(det.bbox.x) // 30, int(det.bbox.y) // 30)
                    if geom not in seen_keys:
                        seen_keys.add(geom)
                        stage1_pairs.append((det, meta))
                        continue
                secondary_pairs.append((det, meta))
            # Prefer compact plate-like primary crops over huge unions for OCR order.
            if len(stage1_pairs) > 1 and fh and fw:
                frame_area = float(max(1, fh * fw))

                def _primary_ocr_rank(item: tuple[Any, Any]) -> float:
                    det = item[0]
                    bw = max(float(det.bbox.w), 1.0)
                    bh = max(float(det.bbox.h), 1.0)
                    area_r = (bw * bh) / frame_area
                    aspect = bw / bh
                    score = float(det.confidence) * 10.0
                    if 2.0 <= aspect <= 6.0 and area_r <= 0.06:
                        score += 8.0
                    elif area_r > 0.08:
                        score -= 6.0
                    return score

                stage1_pairs.sort(key=_primary_ocr_rank, reverse=True)
            # If primary had no plate candidate, fall back to top compact crop once.
            if not stage1_pairs and ordered:
                det0, meta0 = ordered[0]
                stage1_pairs = [(det0, meta0)]
                secondary_pairs = ordered[1:]
            # Primary crop is only a top-band watermark/OSD → also OCR the best
            # mid/lower plate-like secondary crop in Stage-1 (TN51+alamy etc.).
            elif stage1_pairs and secondary_pairs and fh > 0:
                def _is_top_band(det: Any) -> bool:
                    cy = float(det.bbox.y) + float(det.bbox.h) * 0.5
                    return cy < fh * 0.22

                if all(_is_top_band(d) for d, _ in stage1_pairs):
                    def _stage1_alt_score(det: Any) -> float:
                        bw = max(float(det.bbox.w), 1.0)
                        bh = max(float(det.bbox.h), 1.0)
                        aspect = bw / bh
                        area = bw * bh
                        cy = float(det.bbox.y) + bh * 0.5
                        if cy < fh * 0.22:
                            return -1e9
                        if area > float(fh * fw) * 0.12:
                            return -1e9
                        score = float(det.confidence) * 10.0
                        if 1.8 <= aspect <= 6.5:
                            score += 5.0
                        score += min(1.0, cy / max(fh, 1)) * 3.0
                        return score

                    alt = max(secondary_pairs, key=lambda item: _stage1_alt_score(item[0]))
                    if _stage1_alt_score(alt[0]) > -1e8 and len(stage1_pairs) < 2:
                        stage1_pairs.append(alt)
                        secondary_pairs = [p for p in secondary_pairs if p is not alt]
            stage_timing["stage1_primary_candidates"] = len(stage1_pairs)
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
                    "vehicle_index": m.vehicle_index if m else None,
                    "pre_ocr_score": round(float(m.pre_ocr_score), 4) if m else 0.0,
                }
                for d, m in secondary_pairs[:12]
            ]
            plate_dets = [d for d, _ in stage1_pairs]
            plate_assoc_meta = [m for _, m in stage1_pairs]
            _secondary_pairs_held = secondary_pairs
            print(
                f"[ANPR MULTI] stage1_primary_crops={len(stage1_pairs)} "
                f"secondary_parked={len(secondary_pairs)}",
                flush=True,
            )
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
            # Oblique single-line plates are often ~180px tall after pad; refining
            # them clips MH/state glyphs and yields empty OCR. Keep detector crop.
            already_tight_plate = (
                1.6 <= det_aspect <= 5.5
                and det_h <= 240
                and det_w <= 560
                and (fh <= 0 or (det_w * det_h) <= (fh * fw) * 0.18)
            )
            used_refine = False
            pre_refine_crop = crop
            pre_refine_box = padded_box
            pre_refine_xyxy = (x1, y1, x2, y2)
            if not live_mode and not already_tight_plate:
                refined, inner_xyxy, refine_meta = refine_loose_plate_crop(
                    crop, plate_detector=self.plate_detector
                )
                if refined is not None and inner_xyxy is not None:
                    ocr_crop = refined
                    abs_box = offset_bbox(padded_box, inner_xyxy)
                    padded_box = abs_box
                    x1, y1, x2, y2 = abs_box
                    used_refine = True
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
                # Stage-1 keeps consensus so a single confusable misread (D→Y)
                # cannot lock the plate. strong_early_exit (full_plate, no 2nd
                # agreeing pass) is reserved for secondary / retry crops.
                strong_early_exit=False,
            )
            # Refine sometimes over-crops oblique plates → empty OCR. Retry parent crop
            # only when the parent is still a compact plate (not a bumper scene).
            parent_area_ok = True
            if fh and fw and pre_refine_xyxy:
                pw = max(1, int(pre_refine_xyxy[2]) - int(pre_refine_xyxy[0]))
                ph = max(1, int(pre_refine_xyxy[3]) - int(pre_refine_xyxy[1]))
                parent_area_ok = (pw * ph) <= (fh * fw * 0.08)
            if (
                used_refine
                and not live_mode
                and parent_area_ok
                and not is_loose_vehicle_front_crop(pre_refine_crop)
                and not (ensemble.matches_pattern and ensemble.ocr_confident)
                and not strip_plate(ensemble.normalized_text or ensemble.raw_text or "")
                and pre_refine_crop is not None
                and _stage1_calls_used() < MAX_STAGE1_OCR_CALLS
            ):
                retry_ens = self._run_multipass_ocr_profiled(
                    pre_refine_crop,
                    stage="stage1",
                    crop_id=f"plate{idx}_unrefined",
                    bbox=list(pre_refine_xyxy),
                    source="stage1_unrefined_retry",
                    debug_dir=debug_dir,
                    debug_prefix=f"{debug_prefix}_{idx}_unrefined",
                    live_mode=False,
                    early_exit_on_confident=True,
                    adaptive_fast_path=True,
                    strong_early_exit=True,
                )
                if retry_ens.matches_pattern or (
                    strip_plate(retry_ens.normalized_text or retry_ens.raw_text or "")
                    and len(strip_plate(retry_ens.normalized_text or retry_ens.raw_text or ""))
                    > len(strip_plate(ensemble.normalized_text or ensemble.raw_text or ""))
                ):
                    ensemble = retry_ens
                    ocr_crop = pre_refine_crop
                    padded_box = pre_refine_box
                    x1, y1, x2, y2 = pre_refine_xyxy
                    stage_timing["crop_refine_unrefined_retry"] = True
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
                        strong_early_exit=True,
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
            # Offline: if we only read HSRP IND / legend / watermark text, expand
            # right and OCR once more. Empty-crop expand is allowed for single-car
            # scenes (oblique MH12 recovery) but blocked when multiple vehicles
            # already have plate hosts — that path steals neighbors (Tiago).
            crop_text = ensemble.normalized_text or ensemble.raw_text or ""
            crop_compact = strip_plate(crop_text)
            saw_ind_legend = is_plate_marker_noise(crop_text) or (
                "IND" in (crop_text or "").upper()
            )
            non_plate_noise = bool(crop_compact) and is_non_plate_text(crop_text)
            synth_final = int(
                (stage_timing.get("vehicle_synth_meta") or {}).get("final_synthetic") or 0
            )
            hosts_with_plates = {
                m.vehicle_index
                for m in plate_assoc_meta
                if m is not None and m.vehicle_index is not None
            }
            # Empty→HSRP steals neighbors whenever 2+ vehicles already host plates
            # (YOLO multi-car or plate-synth scenes). Single-car / watermark still OK.
            multi_host_scene = len(hosts_with_plates) >= 2 or (
                len(vehicle_refs) >= 3 and synth_final >= 2
            )
            allow_empty_hsrp = not multi_host_scene
            if (
                adaptive_fast_path
                and not live_mode
                and not ensemble.ocr_confident
                and (
                    saw_ind_legend
                    or non_plate_noise
                    or (not crop_compact and allow_empty_hsrp)
                )
                and _stage1_calls_used() < MAX_STAGE1_OCR_CALLS
            ):
                expanded, exp_box = expand_ind_strip_to_hsrp(frame, x1, y1, x2, y2)
                if expanded is not None:
                    # Cap expand whenever multiple vehicles are present.
                    if len(vehicle_refs) >= 2 and exp_box is not None and fh and fw:
                        ew = max(1, int(exp_box[2]) - int(exp_box[0]))
                        eh = max(1, int(exp_box[3]) - int(exp_box[1]))
                        if (ew * eh) > (fh * fw * 0.08):
                            expanded = None
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
                            strong_early_exit=True,
                        )
                        ocr_ms_accum += float(
                            (expanded_ensemble.timing or {}).get("ocr_total_ms") or 0.0
                        )
                        if expanded_ensemble.ocr_confident or (
                            expanded_ensemble.matches_pattern
                            and not is_non_plate_text(
                                expanded_ensemble.normalized_text
                                or expanded_ensemble.raw_text
                                or ""
                            )
                        ):
                            ensemble = expanded_ensemble
                            padded_box = exp_box
                            x1, y1, x2, y2 = exp_box
            # Offline: partial plate fragments (TN51 / Y6552 / bottom line D7249) —
            # widen crop (and grow upward for likely two-line bottoms) then retry once.
            # State+RTO-only on a *wide* single-line crop (TN51 left half) also widens
            # right. Tall two-line motorcycle top-lines (MH12) skip widen → primary-ROI.
            elif (
                adaptive_fast_path
                and not live_mode
                and not ensemble.ocr_confident
                and not is_non_plate_text(crop_text)
                and strip_plate(crop_text)
                and not matches_indian_plate(strip_plate(crop_text))
                and _stage1_calls_used() < MAX_STAGE1_OCR_CALLS
            ):
                compact = strip_plate(crop_text)
                is_state_rto_only = bool(re.match(r"^[A-Z]{2}[0-9]{1,2}$", compact))
                crop_aspect = float(max(1, x2 - x1)) / float(max(1, y2 - y1))
                allow_state_rto_widen = is_state_rto_only and crop_aspect >= 2.0
                # Widen incomplete series+number (Y6552 / D7249) or long scraps.
                # Do NOT widen bare 4-digit BH unique numbers (6517) — that invents
                # false ``17BH6517TA`` from ``BH 6517 TA`` without the year.
                allow_partial_widen = (not is_state_rto_only) and (
                    len(compact) >= 7
                    or bool(re.match(r"^[A-Z]{1,3}[0-9]{2,4}$", compact))
                )
                if allow_state_rto_widen or allow_partial_widen:
                    # Digit-heavy short reads are often the lower row of a two-line bike plate.
                    digit_heavy = sum(ch.isdigit() for ch in compact) >= max(3, len(compact) // 2)
                    grow_up = 1.35 if digit_heavy and len(compact) <= 6 else None
                    clamp_box = None
                    if _should_clamp_plate_expand(getattr(p, "class_name", None)):
                        clamp_box = _host_vehicle_clamp_xyxy(
                            assoc_meta, vehicle_refs, fw=fw, fh=fh
                        )
                    expanded, exp_box = expand_partial_plate_crop(
                        frame,
                        x1,
                        y1,
                        x2,
                        y2,
                        grow_up=grow_up,
                        clamp_xyxy=clamp_box,
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
                            aggressive_early_exit=True,
                            strong_early_exit=True,
                        )
                        ocr_ms_accum += float(
                            (expanded_ensemble.timing or {}).get("ocr_total_ms") or 0.0
                        )
                        if expanded_ensemble.ocr_confident or (
                            expanded_ensemble.matches_pattern
                            and not is_non_plate_text(
                                expanded_ensemble.normalized_text
                                or expanded_ensemble.raw_text
                                or ""
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
            print(
                f"[ANPR MULTI] plate[stage1_{idx}] bbox={[x1, y1, x2, y2]} "
                f"assigned_vehicle={assoc_meta.vehicle_index if assoc_meta else None} "
                f"assignment_method={getattr(assoc_meta, 'assignment_method', None)} "
                f"OCR raw={ensemble.raw_text!r} "
                f"norm={None if non_plate else ensemble.normalized_text!r}",
                flush=True,
            )
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
            # Adaptive early-exit: Stage-1 is primary-only. Stop once a confident
            # primary Indian plate is known (do not burn a 2nd primary bumper crop).
            if (
                (live_mode or adaptive_fast_path)
                and ensemble.ocr_confident
                and ensemble.matches_pattern
                and not non_plate
                and not marker_only
            ):
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
        has_any_pattern = any(
            r.get("matches_pattern") and not r.get("non_plate_text") for r in plates_out
        )
        # Multi-vehicle: prefer per-vehicle secondary OCR over burning the
        # reserved primary-ROI budget on an empty primary bumper crop.
        multi_vehicle_skip_roi = bool(
            len(vehicle_refs) >= 2
            and not live_mode
            and (
                has_any_pattern
                or len(_secondary_pairs_held) >= 2
            )
        )
        stage_timing["primary_roi_invoked"] = False
        stage_timing["primary_roi_skip_reason"] = None
        if live_mode:
            stage_timing["primary_roi_skip_reason"] = "live_mode"
        elif not adaptive_fast_path:
            stage_timing["primary_roi_skip_reason"] = "adaptive_fast_path_off"
        elif has_primary_pattern:
            stage_timing["primary_roi_skip_reason"] = "primary_already_has_pattern"
        elif multi_vehicle_skip_roi:
            stage_timing["primary_roi_skip_reason"] = (
                "multi_vehicle_stage1_hit" if has_any_pattern else "multi_vehicle_secondary_queue"
            )
        run_primary_roi = (
            (not live_mode)
            and adaptive_fast_path
            and (not has_primary_pattern)
            and (not multi_vehicle_skip_roi)
        )
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
                plate_detector=self.plate_detector,
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
                    refined, inner_xyxy, refine_meta = refine_loose_plate_crop(
                        crop, plate_detector=self.plate_detector
                    )
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
                            # Prefer a wide OCR that already contains the full plate
                            # (raw='MH12 AB 5687') — do NOT glue MH12 + that blob into MH12MH12.
                            wide_raw = exp_ens.raw_text or ""
                            wide_norm = sanitize_plate_text(wide_raw) or strip_plate(
                                exp_ens.normalized_text or ""
                            )
                            if matches_indian_plate(wide_norm):
                                stitched = wide_norm
                            else:
                                stitched = stitch_plate_fragments(
                                    crop_text,
                                    exp_ens.normalized_text or "",
                                    wide_raw,
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
                                # Keep the richer multi-token raw when it already encodes both lines.
                                if matches_indian_plate(sanitize_plate_text(wide_raw)):
                                    exp_ens.raw_text = wide_raw
                                else:
                                    exp_ens.raw_text = f"{crop_text} {wide_raw}".strip()
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
                print(
                    f"[ANPR MULTI] plate[primary_roi] bbox={[x1, y1, x2, y2]} "
                    f"assigned_vehicle={assoc_meta.vehicle_index if assoc_meta else None} "
                    f"OCR raw={ensemble.raw_text!r} "
                    f"norm={None if non_plate else ensemble.normalized_text!r}",
                    flush=True,
                )
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

        # Secondary / background OCR — always run in Manual adaptive path so
        # multi-vehicle scenes return every readable plate. Primary ROI has its
        # own reserved budget; this uses a separate per-vehicle early-exit budget.
        has_primary_full = any(
            r.get("matches_pattern")
            and r.get("on_primary_vehicle")
            and not r.get("non_plate_text")
            for r in plates_out
        )
        stage_timing["has_primary_full_before_secondary"] = has_primary_full
        if adaptive_fast_path and not live_mode and _secondary_pairs_held:
            stage_timing["secondary_ocr_invoked"] = True
            frame_area = float(max(1, fh * fw)) if fh and fw else 1.0
            has_any_full = any(
                r.get("matches_pattern") and not r.get("non_plate_text") for r in plates_out
            )

            def _xyxy_iou(a: list[Any], b: list[Any]) -> float:
                ax1, ay1, ax2, ay2 = [float(v) for v in a[:4]]
                bx1, by1, bx2, by2 = [float(v) for v in b[:4]]
                ix1, iy1 = max(ax1, bx1), max(ay1, by1)
                ix2, iy2 = min(ax2, bx2), min(ay2, by2)
                iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
                inter = iw * ih
                if inter <= 0:
                    return 0.0
                ua = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
                ub = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
                union = ua + ub - inter
                return float(inter / union) if union > 0 else 0.0

            resolved_boxes = [
                list(r.get("bbox") or [])
                for r in plates_out
                if r.get("matches_pattern") and not r.get("non_plate_text") and r.get("bbox")
            ]

            def _plate_like_score(det: Any) -> float:
                bw = max(float(det.bbox.w), 1.0)
                bh = max(float(det.bbox.h), 1.0)
                area = bw * bh
                aspect = bw / bh
                cls = str(getattr(det, "class_name", "") or "")
                # Prefer compact plate crops; heavily penalize multi-car unions.
                if area > frame_area * 0.08 and "fallback_wide" not in cls:
                    return -1e9
                if area > frame_area * 0.14:
                    return -1e9
                score = float(det.confidence) * 10.0
                if 1.7 <= aspect <= 6.5:
                    score += 5.0
                # Prefer mid-size plate boxes over tiny empty strips *and* huge unions.
                # (Previously always preferred smaller → empty top strips beat bumper.)
                ideal = frame_area * 0.012
                score -= abs(area - ideal) / frame_area * 25.0
                # Prefer mid/lower road band over OSD / far background strips.
                cy = float(det.bbox.y) + bh * 0.5
                if fh and cy < fh * 0.18:
                    score -= 12.0
                elif fh and cy >= fh * 0.35:
                    score += 3.0
                if area < frame_area * 0.004:
                    score -= 6.0
                # Prefer accepted YOLO plates across vehicles before soft-reject
                # OCR fallbacks (rain/night: do not let v3 burn budget before v2).
                if cls == "yolo_plate":
                    score += 24.0
                elif cls == "yolo_quality_reject_ocr_fallback":
                    score += 14.0
                # City / hybrid wide OpenCV fallback is the intended OCR crop.
                elif cls == "opencv_fallback_wide":
                    score += 18.0
                elif "opencv_fallback" in cls:
                    score += 4.0
                return score

            # One best compact crop per vehicle / geom cluster.
            by_vehicle: dict[Any, list[tuple[Any, Any]]] = {}
            for det, meta in _secondary_pairs_held:
                if _plate_like_score(det) < -1e8:
                    continue
                det_box = [
                    int(det.bbox.x),
                    int(det.bbox.y),
                    int(det.bbox.x + det.bbox.w),
                    int(det.bbox.y + det.bbox.h),
                ]
                # Skip crops already solved in Stage-1 / primary-ROI (night multi).
                if any(_xyxy_iou(det_box, rb) >= 0.45 for rb in resolved_boxes):
                    continue
                vi = meta.vehicle_index if meta is not None else None
                if vi is not None and any(
                    r.get("vehicle_index") == vi
                    and r.get("matches_pattern")
                    and not r.get("non_plate_text")
                    for r in plates_out
                ):
                    continue
                key: Any = vi if vi is not None else ("geom", int(det.bbox.x), int(det.bbox.y))
                by_vehicle.setdefault(key, []).append((det, meta))
            for key in list(by_vehicle.keys()):
                pairs = by_vehicle[key]
                pairs.sort(key=lambda item: _plate_like_score(item[0]), reverse=True)
                # Deduplicate near-identical crops (IoU) before OCR — keep best ranked.
                deduped: list[tuple[Any, Any]] = []
                for det, meta in pairs:
                    box = [
                        int(det.bbox.x),
                        int(det.bbox.y),
                        int(det.bbox.x + det.bbox.w),
                        int(det.bbox.y + det.bbox.h),
                    ]
                    if any(
                        _xyxy_iou(
                            box,
                            [
                                int(d.bbox.x),
                                int(d.bbox.y),
                                int(d.bbox.x + d.bbox.w),
                                int(d.bbox.y + d.bbox.h),
                            ],
                        )
                        >= 0.55
                        for d, _ in deduped
                    ):
                        continue
                    deduped.append((det, meta))
                by_vehicle[key] = deduped[:2]  # at most 2 ranked candidates per vehicle

            def _vehicle_label(vi_key: Any) -> str:
                if not isinstance(vi_key, int):
                    return ""
                if 0 <= vi_key < len(vehicles_out):
                    return str(vehicles_out[vi_key].get("label") or "").lower()
                if 0 <= vi_key < len(vehicles):
                    return str(getattr(vehicles[vi_key], "label", "") or "").lower()
                return ""

            def _vehicle_area_ratio(vi_key: Any) -> float:
                if not isinstance(vi_key, int):
                    return 0.0
                if 0 <= vi_key < len(vehicles_out):
                    try:
                        return float(vehicles_out[vi_key].get("area_ratio") or 0.0)
                    except (TypeError, ValueError):
                        pass
                if 0 <= vi_key < len(vehicle_refs):
                    return float(vehicle_refs[vi_key].area) / frame_area
                return 0.0

            def _is_weak_distant(vi_key: Any) -> bool:
                """Tiny far background cars — skip when enough plates already found."""
                if "motor" in _vehicle_label(vi_key):
                    return False
                area_r = _vehicle_area_ratio(vi_key)
                if area_r >= 0.02:
                    return False
                pairs0 = by_vehicle.get(vi_key) or []
                if not pairs0:
                    return True
                det0 = pairs0[0][0]
                cy = float(det0.bbox.y) + float(det0.bbox.h) * 0.5
                # Top-band distant strips (even opencv_fallback_wide) rarely hold
                # a readable gate plate once the foreground cars are solved.
                if fh and cy < fh * 0.22:
                    return True
                best_score = _plate_like_score(det0)
                return best_score < 10.0

            # Process vehicles with the best plate-like crops first.
            ordered_keys = sorted(
                by_vehicle.keys(),
                key=lambda k: _plate_like_score(by_vehicle[k][0][0]),
                reverse=True,
            )
            stage_timing["secondary_vehicle_count"] = len(ordered_keys)
            stage_timing["secondary_skipped_weak_distant"] = []
            print(
                f"[ANPR MULTI] vehicle_count={len(vehicle_refs)} "
                f"secondary_vehicles={len(ordered_keys)} "
                f"has_primary_full={has_primary_full}",
                flush=True,
            )
            sec_budget = 0
            for vi_key in ordered_keys:
                pairs = by_vehicle[vi_key]
                if sec_budget >= MAX_SECONDARY_OCR_CALLS:
                    stage_timing["secondary_budget_exhausted"] = True
                    break
                valid_now = sum(
                    1
                    for r in plates_out
                    if r.get("matches_pattern") and not r.get("non_plate_text")
                )
                # Prefer recovering the 4 gate cars first; skip empty distant once
                # we already have enough valid plates. Motorcycle always kept.
                if valid_now >= 3 and _is_weak_distant(vi_key):
                    stage_timing["secondary_skipped_weak_distant"].append(
                        {
                            "vehicle": vi_key,
                            "area_ratio": round(_vehicle_area_ratio(vi_key), 4),
                            "valid_plates": valid_now,
                        }
                    )
                    print(
                        f"[ANPR MULTI] vehicle[{vi_key}] skipped weak_distant "
                        f"valid={valid_now} area_r={_vehicle_area_ratio(vi_key):.3f}",
                        flush=True,
                    )
                    continue
                vehicle_hit = False
                cand_n = 0
                # One crop per vehicle by default. A 2nd crop is only for partial
                # fragments (Y6552) — empty/noise must not burn budget needed by
                # other vehicles (Tiago / motorcycle).
                max_cands = 1
                for det, meta in pairs[:2]:
                    if sec_budget >= MAX_SECONDARY_OCR_CALLS:
                        break
                    if vehicle_hit:
                        break
                    if cand_n >= max_cands:
                        break
                    x1, y1, x2, y2 = bbox_to_xyxy(det.bbox.x, det.bbox.y, det.bbox.w, det.bbox.h)
                    # Wide / fallback crops must not pad outside the host vehicle ROI.
                    pad_ratio = self.settings.plate_pad_ratio
                    if str(getattr(det, "class_name", "") or "") == "opencv_fallback_wide":
                        pad_ratio = min(pad_ratio, 0.08)
                    crop, padded_box = crop_with_padding(
                        frame, x1, y1, x2, y2, pad_ratio=pad_ratio
                    )
                    if crop is not None and _should_clamp_plate_expand(
                        getattr(det, "class_name", None)
                    ):
                        clamp_box = _host_vehicle_clamp_xyxy(
                            meta, vehicle_refs, fw=fw, fh=fh
                        )
                        if clamp_box is not None and padded_box is not None:
                            px1, py1, px2, py2 = padded_box
                            cx1, cy1, cx2, cy2 = clamp_box
                            nx1 = max(int(px1), int(cx1))
                            ny1 = max(int(py1), int(cy1))
                            nx2 = min(int(px2), int(cx2))
                            ny2 = min(int(py2), int(cy2))
                            if nx2 - nx1 >= 40 and ny2 - ny1 >= 16:
                                crop = frame[ny1:ny2, nx1:nx2].copy()
                                padded_box = (nx1, ny1, nx2, ny2)
                                x1, y1, x2, y2 = nx1, ny1, nx2, ny2
                                print(
                                    f"[ANPR MULTI] clamped_ocr_crop bbox={[x1, y1, x2, y2]} "
                                    f"host_vehicle_bbox="
                                    f"[{int(cx1)},{int(cy1)},{int(cx2)},{int(cy2)}] "
                                    f"class={getattr(det, 'class_name', None)}",
                                    flush=True,
                                )
                    if crop is None:
                        continue
                    cand_n += 1
                    before = len(ocr_perf.get_session().calls) if ocr_perf.get_session() else 0
                    ensemble = self._run_multipass_ocr_profiled(
                        crop,
                        stage="secondary",
                        crop_id=f"sec_{vi_key}_{sec_budget}",
                        bbox=[x1, y1, x2, y2],
                        source="secondary",
                        live_mode=False,
                        early_exit_on_confident=True,
                        adaptive_fast_path=True,
                        # Must stay False: aggressive_early_exit shares the
                        # primary-ROI call budget and would skip all secondary OCR
                        # after a 12-call ROI miss.
                        aggressive_early_exit=False,
                        # Stop on first strong Indian plate (no consensus 2nd call).
                        strong_early_exit=True,
                    )
                    after = len(ocr_perf.get_session().calls) if ocr_perf.get_session() else before
                    sec_budget += max(0, after - before)
                    stage_timing["ocr_ms"] = round(
                        stage_timing["ocr_ms"]
                        + float((ensemble.timing or {}).get("ocr_total_ms") or 0.0),
                        2,
                    )
                    result_text = ensemble.normalized_text or ensemble.raw_text or ""
                    compact_sec = strip_plate(result_text)
                    # Allow one more crop when first is empty/noise OR a partial
                    # fragment (Y6552) — recovers Creta after a bad bumper box.
                    # After a strong hit, do not spend a 2nd crop on this vehicle.
                    if cand_n == 1 and not (
                        ensemble.matches_pattern and ensemble.ocr_confident
                    ):
                        if (
                            (not compact_sec or is_non_plate_text(result_text))
                            or (
                                compact_sec
                                and not matches_indian_plate(compact_sec)
                                and not is_non_plate_text(result_text)
                                and 4 <= len(compact_sec) <= 8
                            )
                        ):
                            max_cands = 2
                    # HSRP IND legend strip → expand right onto YY BH #### XX.
                    saw_ind = is_plate_marker_noise(result_text) or (
                        "IND" in (result_text or "").upper()
                    )
                    if (
                        saw_ind
                        and not ensemble.matches_pattern
                        and sec_budget < MAX_SECONDARY_OCR_CALLS
                    ):
                        expanded, exp_box = expand_ind_strip_to_hsrp(frame, x1, y1, x2, y2)
                        if expanded is not None and exp_box is not None and fh and fw:
                            ew = max(1, int(exp_box[2]) - int(exp_box[0]))
                            eh = max(1, int(exp_box[3]) - int(exp_box[1]))
                            if (ew * eh) > (fh * fw * 0.08):
                                expanded = None
                        if expanded is not None:
                            before2 = (
                                len(ocr_perf.get_session().calls) if ocr_perf.get_session() else 0
                            )
                            expanded_ensemble = self._run_multipass_ocr_profiled(
                                expanded,
                                stage="secondary_hsrp",
                                crop_id=f"sec_{vi_key}_hsrp",
                                bbox=list(exp_box) if exp_box else [x1, y1, x2, y2],
                                source="secondary_hsrp_expand",
                                live_mode=False,
                                early_exit_on_confident=True,
                                adaptive_fast_path=True,
                                aggressive_early_exit=False,
                                strong_early_exit=True,
                            )
                            after2 = (
                                len(ocr_perf.get_session().calls)
                                if ocr_perf.get_session()
                                else before2
                            )
                            sec_budget += max(0, after2 - before2)
                            stage_timing["ocr_ms"] = round(
                                stage_timing["ocr_ms"]
                                + float(
                                    (expanded_ensemble.timing or {}).get("ocr_total_ms") or 0.0
                                ),
                                2,
                            )
                            if expanded_ensemble.ocr_confident or (
                                expanded_ensemble.matches_pattern
                                and not is_non_plate_text(
                                    expanded_ensemble.normalized_text
                                    or expanded_ensemble.raw_text
                                    or ""
                                )
                            ):
                                ensemble = expanded_ensemble
                                padded_box = exp_box or padded_box
                                if exp_box:
                                    x1, y1, x2, y2 = exp_box
                                result_text = (
                                    ensemble.normalized_text or ensemble.raw_text or ""
                                )
                                compact_sec = strip_plate(result_text)
                    # Offline: partial lower-line fragments (Y6552) — widen once.
                    elif (
                        not ensemble.ocr_confident
                        and compact_sec
                        and not matches_indian_plate(compact_sec)
                        and not is_non_plate_text(result_text)
                        and len(compact_sec) >= 4
                        and sec_budget < MAX_SECONDARY_OCR_CALLS
                    ):
                        clamp_box = None
                        if _should_clamp_plate_expand(getattr(det, "class_name", None)):
                            clamp_box = _host_vehicle_clamp_xyxy(
                                meta, vehicle_refs, fw=fw, fh=fh
                            )
                        expanded, exp_box = expand_partial_plate_crop(
                            frame, x1, y1, x2, y2, clamp_xyxy=clamp_box
                        )
                        if expanded is not None:
                            before2 = (
                                len(ocr_perf.get_session().calls) if ocr_perf.get_session() else 0
                            )
                            expanded_ensemble = self._run_multipass_ocr_profiled(
                                expanded,
                                stage="secondary_wide",
                                crop_id=f"sec_{vi_key}_wide",
                                bbox=list(exp_box) if exp_box else [x1, y1, x2, y2],
                                source="secondary_partial_expand",
                                live_mode=False,
                                early_exit_on_confident=True,
                                adaptive_fast_path=True,
                                aggressive_early_exit=False,
                                strong_early_exit=True,
                            )
                            after2 = (
                                len(ocr_perf.get_session().calls) if ocr_perf.get_session() else before2
                            )
                            sec_budget += max(0, after2 - before2)
                            stage_timing["ocr_ms"] = round(
                                stage_timing["ocr_ms"]
                                + float(
                                    (expanded_ensemble.timing or {}).get("ocr_total_ms") or 0.0
                                ),
                                2,
                            )
                            if expanded_ensemble.ocr_confident or (
                                expanded_ensemble.matches_pattern
                                and not is_non_plate_text(
                                    expanded_ensemble.normalized_text
                                    or expanded_ensemble.raw_text
                                    or ""
                                )
                            ):
                                ensemble = expanded_ensemble
                                padded_box = exp_box or padded_box
                                if exp_box:
                                    x1, y1, x2, y2 = exp_box
                                result_text = (
                                    ensemble.normalized_text or ensemble.raw_text or ""
                                )
                    multi = extract_all_indian_plates(
                        f"{ensemble.raw_text or ''} {ensemble.normalized_text or ''}"
                    )
                    if not multi and ensemble.normalized_text and matches_indian_plate(
                        strip_plate(ensemble.normalized_text)
                    ):
                        multi = [strip_plate(ensemble.normalized_text)]
                    print(
                        f"[ANPR MULTI] plate[secondary] bbox={[x1, y1, x2, y2]} "
                        f"assigned_vehicle={meta.vehicle_index if meta else None} "
                        f"assignment_method={getattr(meta, 'assignment_method', None)} "
                        f"OCR raw={ensemble.raw_text!r} plates={multi!r}",
                        flush=True,
                    )
                    if not multi:
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
                                "matches_pattern": False,
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
                                "vehicle_containment": round(float(meta.containment), 4)
                                if meta
                                else 0.0,
                                "secondary_stage": True,
                                "selected_variant": ensemble.selected_variant,
                                "ocr_passes": [],
                                "debug_crops": [],
                                "ocr_timing": ensemble.timing or {},
                            }
                        )
                    else:
                        for plate_text in multi:
                            plates_out.append(
                                {
                                    "raw_text": ensemble.raw_text,
                                    "normalized_text": plate_text,
                                    "normalized_plate": plate_text,
                                    "ocr_confident": True,
                                    "confidence": round(
                                        float(det.confidence) * 0.35
                                        + float(ensemble.ocr_confidence or 0.0) * 0.65,
                                        4,
                                    ),
                                    "ocr_confidence": round(
                                        float(ensemble.ocr_confidence or 0.0), 4
                                    ),
                                    "plate_confidence": round(float(det.confidence), 4),
                                    "matches_pattern": True,
                                    "marker_noise": False,
                                    "non_plate_text": False,
                                    "bbox": [x1, y1, x2, y2],
                                    "padded_bbox": list(padded_box)
                                    if padded_box
                                    else [x1, y1, x2, y2],
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
                                    "vehicle_containment": round(float(meta.containment), 4)
                                    if meta
                                    else 0.0,
                                    "secondary_stage": True,
                                    "selected_variant": ensemble.selected_variant,
                                    "ocr_passes": [],
                                    "debug_crops": [],
                                    "ocr_timing": ensemble.timing or {},
                                }
                            )
                        vehicle_hit = True
                        break  # early-exit this vehicle only
                print(
                    f"[ANPR MULTI] vehicle[{vi_key}] done hit={vehicle_hit} "
                    f"plate_candidates={cand_n} sec_budget={sec_budget}/{MAX_SECONDARY_OCR_CALLS}",
                    flush=True,
                )

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
        all_frags = _collect_stitch_fragments(primary_only=False)
        stitched_primary = stitch_plate_fragments(*(t for _, t in primary_frags)) if primary_frags else None
        stitched_all = stitch_plate_fragments(*(t for _, t in all_frags)) if all_frags else None
        stage_timing["stitch_primary_fragments"] = [t for _, t in primary_frags][:12]
        stage_timing["stitch_primary_result"] = stitched_primary
        stage_timing["stitch_all_result"] = stitched_all

        def _stitch_quality(text: str | None, n_frags: int) -> float:
            if not text or not matches_indian_plate(text):
                return -1e9
            score = float(len(text))
            score -= max(0, n_frags - 2) * 12.0
            if re.search(r"[A-Z]{1,3}[0-9]{3,4}$", text):
                score += 15.0
            return score

        existing_full = next(
            (
                r.get("normalized_text")
                for r in plates_out
                if r.get("matches_pattern")
                and not r.get("non_plate_text")
                and not r.get("stitched_from_fragments")
            ),
            None,
        )
        candidates: list[tuple[float, str, list[tuple[float, str]]]] = []
        if stitched_all:
            candidates.append(
                (_stitch_quality(stitched_all, len(all_frags)), stitched_all, all_frags)
            )
        if stitched_primary:
            candidates.append(
                (
                    _stitch_quality(stitched_primary, len(primary_frags)),
                    stitched_primary,
                    primary_frags,
                )
            )
        candidates.sort(key=lambda item: item[0], reverse=True)
        if candidates and candidates[0][0] > -1e8:
            score, chosen_stitch, chosen_frags = candidates[0]
            existing_score = _stitch_quality(existing_full, 2) if existing_full else -1e9
            if existing_full is None or score > existing_score:
                _apply_stitch(chosen_frags, chosen_stitch)
                stage_timing["stitched_applied"] = chosen_stitch

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

        # Per-vehicle results (live + Manual). One best plate per vehicle; plates
        # stay associated via vehicle_index / track_id (never reassigned across vehicles).
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
            # Prefer longer complete plates (MH12UF7286 over truncated MH12UF728).
            owned_reliable.sort(
                key=lambda p: (
                    len(str(p.get("normalized_text") or "")),
                    float(p.get("ocr_confidence") or 0.0),
                    float(p.get("confidence") or 0.0),
                ),
                reverse=True,
            )
            best_v = owned_reliable[0] if owned_reliable else (owned[0] if owned else None)
            print(
                f"[ANPR MULTI] vehicle[{vi}] bbox={list(vrow.get('bbox') or [])} "
                f"source={vrow.get('source')} area_ratio={vrow.get('area_ratio')} "
                f"plate_candidates={len(owned)} "
                f"OCR={(best_v or {}).get('normalized_text') if best_v else None} "
                f"raw={(best_v or {}).get('raw_text') if best_v else None}",
                flush=True,
            )
            vehicle_results.append(
                {
                    "vehicle_id": vi,
                    "track_id": tid,
                    "vehicle_index": vi,
                    "is_primary": bool(vrow.get("is_primary")),
                    "vehicle_bbox": list(vrow.get("bbox") or []),
                    "bbox": list(vrow.get("bbox") or []),
                    "confidence": vrow.get("confidence"),
                    "plate": (best_v or {}).get("normalized_text") if best_v else None,
                    "best_plate": (best_v or {}).get("normalized_text") if best_v else None,
                    "best_raw": (best_v or {}).get("raw_text") if best_v else None,
                    "plate_bbox": list((best_v or {}).get("bbox") or []) if best_v else [],
                    "matches_pattern": bool((best_v or {}).get("matches_pattern")) if best_v else False,
                    "ocr_confidence": (best_v or {}).get("ocr_confidence") if best_v else None,
                    "plate_confidence": (best_v or {}).get("plate_confidence") if best_v else None,
                    "combined_confidence": (best_v or {}).get("confidence") if best_v else None,
                    "plate_count": len(owned),
                }
            )
        # Attach final plates onto hybrid per-vehicle logs (same vehicle order).
        hybrid_logs = stage_timing.get("hybrid_vehicle_logs")
        if isinstance(hybrid_logs, list) and hybrid_logs:
            by_idx = {vr.get("vehicle_index"): vr for vr in vehicle_results}
            for hi, h in enumerate(hybrid_logs):
                vid = h.get("vehicle_id")
                if vid is None:
                    vid = hi
                    h["vehicle_id"] = vid
                vr = by_idx.get(vid) or by_idx.get(hi)
                if vr:
                    h["final_plate"] = vr.get("best_plate") or vr.get("plate")
                    h["final_raw"] = vr.get("best_raw")
                    h["ocr_plate_candidates"] = vr.get("plate_count")
                print(
                    f"[ANPR HYBRID] vehicle_id={h.get('vehicle_id')} "
                    f"yolo_n={h.get('yolo_plate_candidates')} "
                    f"yolo_selected_conf={h.get('yolo_selected_conf')} "
                    f"yolo_best_conf={h.get('yolo_best_conf')} "
                    f"fallback_used={h.get('fallback_used')} "
                    f"opencv_n={h.get('opencv_candidates')} "
                    f"final_plate={h.get('final_plate')}",
                    flush=True,
                )
        # Flat detections list: one entry per valid vehicle+plate association.
        detections: list[dict[str, Any]] = []
        seen_plates: set[str] = set()
        for vr in vehicle_results:
            plate = vr.get("best_plate") or vr.get("plate")
            if not plate or not vr.get("matches_pattern"):
                continue
            plate = str(plate)
            if plate in seen_plates:
                continue
            seen_plates.add(plate)
            detections.append(
                {
                    "vehicle_id": vr.get("vehicle_id"),
                    "track_id": vr.get("track_id"),
                    "vehicle_bbox": list(vr.get("vehicle_bbox") or vr.get("bbox") or []),
                    "plate_bbox": list(vr.get("plate_bbox") or []),
                    "plate": plate,
                    "raw_ocr": vr.get("best_raw"),
                    "ocr_confidence": float(vr.get("ocr_confidence") or 0.0),
                    "plate_confidence": float(vr.get("plate_confidence") or 0.0),
                    "confidence": float(vr.get("combined_confidence") or 0.0),
                    "combined_confidence": float(vr.get("combined_confidence") or 0.0),
                    "is_primary": bool(vr.get("is_primary")),
                    "matches_indian_pattern": True,
                }
            )
        # Also include reliable plates not tied to a vehicle row (rare full-frame hits).
        for p in reliable:
            plate = p.get("normalized_text") or p.get("normalized_plate")
            if not plate:
                continue
            plate = str(plate)
            if plate in seen_plates:
                continue
            seen_plates.add(plate)
            detections.append(
                {
                    "vehicle_id": p.get("vehicle_index"),
                    "track_id": p.get("track_id"),
                    "vehicle_bbox": [],
                    "plate_bbox": list(p.get("bbox") or []),
                    "plate": plate,
                    "raw_ocr": p.get("raw_text"),
                    "ocr_confidence": float(p.get("ocr_confidence") or 0.0),
                    "plate_confidence": float(p.get("plate_confidence") or 0.0),
                    "confidence": float(p.get("confidence") or 0.0),
                    "combined_confidence": float(p.get("confidence") or 0.0),
                    "is_primary": bool(p.get("on_primary_vehicle")),
                    "matches_indian_pattern": True,
                }
            )
        # Drop truncated stems when a longer valid extension exists (MH12UF728 vs …7286).
        plate_texts = [str(d.get("plate") or "") for d in detections]
        dropped_primary_stems: list[str] = []
        kept: list[dict[str, Any]] = []
        for d in detections:
            plate = str(d.get("plate") or "")
            longer = next(
                (
                    q
                    for q in plate_texts
                    if q != plate and q.startswith(plate) and matches_indian_plate(q)
                ),
                None,
            )
            if longer:
                if d.get("is_primary"):
                    dropped_primary_stems.append(longer)
                continue
            kept.append(d)
        for d in kept:
            if str(d.get("plate") or "") in dropped_primary_stems:
                d["is_primary"] = True
        detections = kept
        # Prefer primary / longer plates first for Manual UI selection.
        detections.sort(
            key=lambda d: (
                1 if d.get("is_primary") else 0,
                len(str(d.get("plate") or "")),
                float(d.get("combined_confidence") or 0.0),
            ),
            reverse=True,
        )
        stage_timing["vehicle_results"] = vehicle_results
        ocr_vehicles = sum(
            1
            for vr in vehicle_results
            if vr.get("plate") or vr.get("best_plate") or int(vr.get("plate_count") or 0) > 0
        )
        stage_timing["ocr_vehicles_processed"] = ocr_vehicles
        print(
            f"[ANPR ROI] ocr_vehicles_processed={ocr_vehicles} "
            f"roi_enabled={stage_timing.get('roi_enabled')} "
            f"ignored_outside_roi={stage_timing.get('ignored_outside_roi')}",
            flush=True,
        )
        stage_timing["detections_count"] = len(detections)
        if self.vehicle_tracker is not None and live_mode:
            try:
                stage_timing["active_tracks"] = self.vehicle_tracker.snapshot()
            except Exception:  # noqa: BLE001
                stage_timing["active_tracks"] = []

        return {
            "vehicles": vehicles_out,
            "vehicle_results": vehicle_results,
            "detections": detections,
            "plates": plates_out,
            "vehicle_detected": bool(vehicles_out),
            # Only true when at least one Indian-pattern plate was recovered.
            "plate_detected": bool(reliable),
            "error": None if reliable else ("no reliable plate detected" if plates_out else None),
            "timing": stage_timing,
        }
