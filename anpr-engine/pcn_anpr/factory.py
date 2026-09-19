from __future__ import annotations

"""Provider factory — builds replaceable detectors/OCR from settings."""

import logging
from typing import Any

from pcn_anpr.ai_plate import AILicensePlateDetector
from pcn_anpr.config import ANPRSettings, get_anpr_settings
from pcn_anpr.interfaces import OCRProvider, PlateDetection, PlateDetector, VehicleDetection, VehicleDetector
from pcn_anpr.mock_providers import MockOCRProvider, MockPlateDetector, MockVehicleDetector
from pcn_anpr.opencv_plate import OpenCVPlateDetector
from pcn_anpr.opencv_vehicle import OpenCVVehicleDetector
from pcn_anpr.paddle_ocr import EmptyOCRProvider, PaddleOCRProvider
from pcn_anpr.plate_modes import AIWithOpenCVFallback, ComparePlateDetector

logger = logging.getLogger(__name__)


class NoOpPlateDetector(PlateDetector):
    def detect(self, frame: Any, vehicle: VehicleDetection | None = None) -> list[PlateDetection]:
        return []


class NoOpVehicleDetector(VehicleDetector):
    detector_name = "noop"

    def detect(self, frame: Any) -> list[VehicleDetection]:
        return []


def _yolo_available() -> bool:
    try:
        import ultralytics  # noqa: F401

        return True
    except ImportError:
        return False


def build_vehicle_detector(settings: ANPRSettings | None = None) -> VehicleDetector:
    """Select vehicle detector from ``ANPR_VEHICLE_DETECTOR`` (default: yolo).

    Modes:
    - ``yolo`` — Ultralytics YOLOv8n (AGPL-3.0); falls back to OpenCV if unavailable
    - ``opencv`` — contour heuristics (Apache 2.0 fallback)
    - ``mock`` — synthetic box (also used when ``ANPR_PROVIDER_MODE=mock``)
    """
    settings = settings or get_anpr_settings()
    mode = (getattr(settings, "vehicle_detector", None) or "yolo").strip().lower()

    if settings.provider_mode == "mock" or mode == "mock":
        return MockVehicleDetector()
    if not settings.anpr_enabled or not settings.vehicle_detector_enabled:
        return NoOpVehicleDetector()

    if mode == "opencv":
        logger.info("vehicle_detector=opencv (configured)")
        return OpenCVVehicleDetector()

    # Default / yolo path
    if mode == "yolo" and _yolo_available():
        from pcn_anpr.yolo_vehicle import YOLOVehicleDetector

        weights = (getattr(settings, "yolo_vehicle_weights", None) or "").strip() or None
        det = YOLOVehicleDetector(
            weights=weights,
            model_dir=settings.model_dir,
            conf=float(getattr(settings, "yolo_vehicle_conf", 0.35) or 0.35),
        )
        logger.info(
            "vehicle_detector=yolo weights=%s conf=%s",
            weights or f"{settings.model_dir}/vehicle/yolov8n.pt",
            getattr(settings, "yolo_vehicle_conf", 0.35),
        )
        return det

    if mode == "yolo":
        logger.warning(
            "vehicle_detector=yolo requested but ultralytics unavailable; "
            "falling back to opencv (install anpr-engine/requirements-yolo.txt)"
        )
    else:
        logger.warning("vehicle_detector=%s unknown; using opencv", mode)
    return OpenCVVehicleDetector()

def _build_opencv_plate(settings: ANPRSettings) -> PlateDetector:
    return OpenCVPlateDetector(min_confidence=settings.min_plate_confidence)


def _build_yolo_plate(settings: ANPRSettings) -> PlateDetector:
    from pcn_anpr.yolo_plate import YOLOPlateDetector

    weights = (getattr(settings, "yolo_plate_weights", None) or "").strip() or None
    return YOLOPlateDetector(
        weights=weights,
        model_dir=settings.model_dir,
        conf=float(getattr(settings, "yolo_plate_conf", 0.25) or 0.25),
        min_confidence=float(settings.min_plate_confidence),
        allow_download=True,
    )


def _build_ai_plate(settings: ANPRSettings) -> AILicensePlateDetector:
    return AILicensePlateDetector(
        model_dir=settings.ai_plate_model_dir,
        min_confidence=settings.min_plate_confidence,
        eager=False,
    )


def build_plate_detector(settings: ANPRSettings | None = None) -> PlateDetector:
    """Select plate detector from ``ANPR_PLATE_DETECTOR`` (default: opencv).

    Modes:
    - ``opencv`` — production default (morphology heuristics)
    - ``yolo`` — plate-specific YOLOv8n (EVAL)
    - ``hybrid`` — YOLO per vehicle, OpenCV only when YOLO has no usable plate
    - ``ai`` — AI primary with OpenCV fallback when AI returns nothing
    - ``compare`` — run both; log both; use OpenCV for OCR / events
    """
    settings = settings or get_anpr_settings()
    if settings.provider_mode == "mock":
        return MockPlateDetector()
    if not settings.anpr_enabled or not settings.plate_detector_enabled:
        return NoOpPlateDetector()

    mode = (settings.plate_detector or "opencv").strip().lower()
    if mode == "hybrid":
        if _yolo_available():
            from pcn_anpr.hybrid_plate import HybridPlateDetector

            logger.info("plate_detector=hybrid (YOLO first, OpenCV per-vehicle fallback)")
            return HybridPlateDetector(_build_yolo_plate(settings), _build_opencv_plate(settings))
        logger.warning("plate_detector=hybrid but ultralytics unavailable; using opencv")
        return _build_opencv_plate(settings)
    if mode == "yolo":
        if _yolo_available():
            logger.info(
                "plate_detector=yolo weights=%s conf=%s (EVAL — not Indian-validated default)",
                getattr(settings, "yolo_plate_weights", None) or f"{settings.model_dir}/plate/…",
                getattr(settings, "yolo_plate_conf", 0.25),
            )
            return _build_yolo_plate(settings)
        logger.warning(
            "plate_detector=yolo requested but ultralytics unavailable; "
            "falling back to opencv"
        )
        return _build_opencv_plate(settings)
    if mode == "ai":
        return AIWithOpenCVFallback(_build_ai_plate(settings), _build_opencv_plate(settings))
    if mode == "compare":
        return ComparePlateDetector(_build_opencv_plate(settings), _build_ai_plate(settings))
    # Default / unknown → OpenCV (never silently switch to YOLO/AI)
    return _build_opencv_plate(settings)


def build_ocr(settings: ANPRSettings | None = None) -> OCRProvider:
    settings = settings or get_anpr_settings()
    if settings.provider_mode == "mock":
        return MockOCRProvider()
    if not settings.ocr_enabled or not settings.anpr_enabled:
        return EmptyOCRProvider()
    # Eager=False here; LiveAnprWorker / warm_up() call initialize() once at startup.
    return PaddleOCRProvider(eager=False)


def build_pipeline(settings: ANPRSettings | None = None):
    from pcn_anpr.pipeline import ANPRPipeline

    settings = settings or get_anpr_settings()
    return ANPRPipeline(
        vehicle_detector=build_vehicle_detector(settings),
        plate_detector=build_plate_detector(settings),
        ocr=build_ocr(settings),
        settings=settings,
    )
