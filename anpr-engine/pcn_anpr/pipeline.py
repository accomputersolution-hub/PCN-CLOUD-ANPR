from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from pcn_anpr.config import ANPRSettings, get_anpr_settings
from pcn_anpr.image_io import bbox_to_xyxy, load_bgr
from pcn_anpr.interfaces import (
    OCRProvider,
    OCRResult,
    PipelineResult,
    PlateDetector,
    VehicleDetector,
)
from pcn_anpr.mock_providers import MockOCRProvider, MockPlateDetector, MockVehicleDetector
from pcn_anpr.ocr_ensemble import run_multipass_ocr
from pcn_anpr.preprocess import crop_with_padding


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

    def _ocr_scales(self) -> tuple[float, ...]:
        scales: list[float] = []
        if self.settings.ocr_upscale_2x:
            scales.append(2.0)
        if self.settings.ocr_upscale_3x:
            scales.append(3.0)
        return tuple(scales) or (2.0,)

    def warm_up(self) -> dict[str, Any]:
        """Load OCR models once and run a dummy inference. Call before live loop."""
        import numpy as np

        started = time.perf_counter()
        if hasattr(self.ocr, "initialize"):
            self.ocr.initialize()
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
        started = time.perf_counter()
        from pcn_anpr.image_io import describe_image_path, resolve_image_path

        supplied = image_path
        path = resolve_image_path(image_path)
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
                base["processing_ms"] = int((time.perf_counter() - started) * 1000)
                return base
            frame = load_bgr(path)
            if frame is None:
                base["error"] = "invalid_image"
                base["path_debug"] = describe_image_path(supplied, path)
                base["processing_ms"] = int((time.perf_counter() - started) * 1000)
                return base
            result = self._infer(frame, debug_prefix=path.stem)
            result["filename"] = path.name
            result["path"] = str(path)
            result["supplied_path"] = str(supplied)
            result["decoded_shape"] = list(frame.shape) if hasattr(frame, "shape") else None
            result["processing_ms"] = int((time.perf_counter() - started) * 1000)
            return result
        except Exception as exc:  # noqa: BLE001
            base["error"] = str(exc)
            base["processing_ms"] = int((time.perf_counter() - started) * 1000)
            return base

    def _infer(self, frame: Any, *, debug_prefix: str = "plate", live_mode: bool = False) -> dict[str, Any]:
        vehicles_out: list[dict[str, Any]] = []
        plates_out: list[dict[str, Any]] = []

        if frame is None:
            return {
                "vehicles": [],
                "plates": [],
                "vehicle_detected": False,
                "plate_detected": False,
                "error": None,
            }

        vehicles = []
        try:
            vehicles = self.vehicle_detector.detect(frame) or []
        except Exception:
            vehicles = []

        for v in vehicles:
            x1, y1, x2, y2 = bbox_to_xyxy(v.bbox.x, v.bbox.y, v.bbox.w, v.bbox.h)
            vehicles_out.append(
                {
                    "label": v.label,
                    "confidence": float(v.confidence),
                    "bbox": [x1, y1, x2, y2],
                }
            )

        plate_dets = []
        try:
            if vehicles:
                for v in vehicles:
                    plate_dets.extend(self.plate_detector.detect(frame, v) or [])
            if not plate_dets:
                plate_dets = self.plate_detector.detect(frame, None) or []
        except Exception:
            plate_dets = []

        plate_dets = sorted(plate_dets, key=lambda p: p.confidence, reverse=True)
        # Live: OCR only the best candidate — multipass on many boxes is multi-second on CPU
        max_plates = 1 if live_mode else len(plate_dets)
        plate_dets = plate_dets[:max_plates]

        for idx, p in enumerate(plate_dets):
            if p.confidence < self.settings.min_plate_confidence:
                continue
            x1, y1, x2, y2 = bbox_to_xyxy(p.bbox.x, p.bbox.y, p.bbox.w, p.bbox.h)
            crop, padded_box = crop_with_padding(
                frame,
                x1,
                y1,
                x2,
                y2,
                pad_ratio=self.settings.plate_pad_ratio,
            )
            if crop is None:
                continue

            # Live never writes OCR debug crops unless settings explicitly keep them AND not live
            debug_dir = None
            if self.settings.ocr_save_debug_crops and not live_mode:
                debug_dir = self.settings.ocr_debug_dir
            ensemble = run_multipass_ocr(
                crop,
                self.ocr,
                min_ocr_confidence=self.settings.min_ocr_confidence,
                confusable_substitution=self.settings.confusable_substitution,
                debug_dir=debug_dir,
                debug_prefix=f"{debug_prefix}_{idx}",
                scales=self._ocr_scales(),
                live_mode=live_mode,
                early_exit_on_confident=live_mode,
            )

            combined = (
                float(p.confidence) * 0.35 + ensemble.ocr_confidence * 0.65
                if ensemble.raw_text
                else float(p.confidence) * 0.3
            )
            plates_out.append(
                {
                    "raw_text": ensemble.raw_text,
                    "normalized_text": ensemble.normalized_text,
                    "normalized_plate": ensemble.normalized_text,
                    "ocr_confident": ensemble.ocr_confident,
                    "confidence": round(combined, 4),
                    "ocr_confidence": round(ensemble.ocr_confidence, 4),
                    "plate_confidence": round(float(p.confidence), 4),
                    "matches_pattern": ensemble.matches_pattern,
                    "bbox": [x1, y1, x2, y2],
                    "padded_bbox": list(padded_box),
                    "selected_variant": ensemble.selected_variant,
                    "ocr_passes": [
                        {
                            "variant": pr.variant,
                            "raw_text": pr.raw_text,
                            "normalized": pr.normalized,
                            "confidence": pr.confidence,
                            "matches_pattern": pr.matches_pattern,
                        }
                        for pr in ensemble.passes
                    ],
                    "debug_crops": ensemble.debug_crops,
                }
            )
            if live_mode and ensemble.ocr_confident:
                break

        plates_out.sort(
            key=lambda row: (
                1 if row.get("ocr_confident") else 0,
                1 if row.get("matches_pattern") else 0,
                len(row.get("raw_text") or ""),
                row.get("confidence") or 0,
            ),
            reverse=True,
        )

        return {
            "vehicles": vehicles_out,
            "plates": plates_out,
            "vehicle_detected": bool(vehicles_out),
            "plate_detected": bool(plates_out),
            "error": None,
        }
