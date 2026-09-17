from __future__ import annotations

"""Provider factory — builds replaceable detectors/OCR from settings."""

from typing import Any

from pcn_anpr.config import ANPRSettings, get_anpr_settings
from pcn_anpr.interfaces import OCRProvider, PlateDetection, PlateDetector, VehicleDetection, VehicleDetector
from pcn_anpr.mock_providers import MockOCRProvider, MockPlateDetector, MockVehicleDetector
from pcn_anpr.opencv_plate import OpenCVPlateDetector
from pcn_anpr.opencv_vehicle import OpenCVVehicleDetector
from pcn_anpr.paddle_ocr import EmptyOCRProvider, PaddleOCRProvider


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


def build_plate_detector(settings: ANPRSettings | None = None) -> PlateDetector:
    settings = settings or get_anpr_settings()
    if settings.provider_mode == "mock":
        return MockPlateDetector()
    if not settings.anpr_enabled or not settings.plate_detector_enabled:
        return NoOpPlateDetector()
    return OpenCVPlateDetector(min_confidence=settings.min_plate_confidence)


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
