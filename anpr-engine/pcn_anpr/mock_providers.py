from __future__ import annotations

from pcn_anpr.interfaces import (
    BoundingBox,
    OCRProvider,
    OCRResult,
    PlateDetection,
    PlateDetector,
    VehicleDetection,
    VehicleDetector,
)


class MockVehicleDetector(VehicleDetector):
    def detect(self, frame: object) -> list[VehicleDetection]:
        return [VehicleDetection(bbox=BoundingBox(0.1, 0.2, 0.7, 0.6, 0.88), confidence=0.88)]


class MockPlateDetector(PlateDetector):
    def detect(self, frame: object, vehicle: VehicleDetection | None = None) -> list[PlateDetection]:
        return [PlateDetection(bbox=BoundingBox(0.35, 0.55, 0.3, 0.12, 0.91), confidence=0.91)]


class MockOCRProvider(OCRProvider):
    def __init__(self, text: str = "MH12AB1234", confidence: float = 0.94) -> None:
        self.text = text
        self.confidence = confidence
        self._init_ms = 0.0
        self.instance_id = id(self)

    def initialize(self) -> bool:
        return True

    @property
    def init_ms(self) -> float:
        return self._init_ms

    def read(self, plate_crop: object) -> OCRResult:
        return OCRResult(text=self.text, confidence=self.confidence, raw_text=self.text)
