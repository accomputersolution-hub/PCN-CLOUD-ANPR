from __future__ import annotations

"""Provider factory — builds replaceable detectors/OCR from settings."""

from typing import Any

from pcn_anpr.ai_plate import AILicensePlateDetector
from pcn_anpr.config import ANPRSettings, get_anpr_settings
from pcn_anpr.interfaces import OCRProvider, PlateDetection, PlateDetector, VehicleDetection, VehicleDetector
from pcn_anpr.mock_providers import MockOCRProvider, MockPlateDetector, MockVehicleDetector
from pcn_anpr.opencv_plate import OpenCVPlateDetector
from pcn_anpr.opencv_vehicle import OpenCVVehicleDetector
from pcn_anpr.paddle_ocr import EmptyOCRProvider, PaddleOCRProvider
from pcn_anpr.plate_modes import AIWithOpenCVFallback, ComparePlateDetector


class NoOpPlateDetector(PlateDetector):
    def detect(self, frame: Any, vehicle: VehicleDetection | None = None) -> list[PlateDetection]:
        return []


class NoOpVehicleDetector(VehicleDetector):
    def detect(self, frame: Any) -> list[VehicleDetection]:
        return []


def build_vehicle_detector(settings: ANPRSettings | None = None) -> VehicleDetector:
    settings = settings or get_anpr_settings()
    if settings.provider_mode == "mock":
        return MockVehicleDetector()
    if not settings.anpr_enabled or not settings.vehicle_detector_enabled:
        return NoOpVehicleDetector()
    return OpenCVVehicleDetector()


def _build_opencv_plate(settings: ANPRSettings) -> PlateDetector:
    return OpenCVPlateDetector(min_confidence=settings.min_plate_confidence)


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
    - ``ai`` — AI primary with OpenCV fallback when AI returns nothing
    - ``compare`` — run both; log both; use OpenCV for OCR / events
    """
    settings = settings or get_anpr_settings()
    if settings.provider_mode == "mock":
        return MockPlateDetector()
    if not settings.anpr_enabled or not settings.plate_detector_enabled:
        return NoOpPlateDetector()

    mode = (settings.plate_detector or "opencv").strip().lower()
    if mode == "ai":
        return AIWithOpenCVFallback(_build_ai_plate(settings), _build_opencv_plate(settings))
    if mode == "compare":
        return ComparePlateDetector(_build_opencv_plate(settings), _build_ai_plate(settings))
    # Default / unknown → OpenCV (never silently switch to AI)
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
