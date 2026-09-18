from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BoundingBox:
    x: float
    y: float
    w: float
    h: float
    confidence: float


@dataclass
class VehicleDetection:
    bbox: BoundingBox
    label: str = "vehicle"
    confidence: float = 0.0
    track_id: str | None = None


@dataclass
class PlateDetection:
    bbox: BoundingBox
    confidence: float = 0.0
    class_name: str = "license_plate"
    timing_ms: float | None = None


@dataclass
class OCRResult:
    text: str
    confidence: float
    raw_text: str


@dataclass
class PipelineResult:
    vehicle: VehicleDetection | None
    plate: PlateDetection | None
    ocr: OCRResult | None
    processing_ms: int
    extras: dict[str, Any] = field(default_factory=dict)


class VehicleDetector(ABC):
    @abstractmethod
    def detect(self, frame: Any) -> list[VehicleDetection]:
        """Detect vehicles in a camera frame. Frame type is backend-specific (ndarray, bytes, etc.)."""


class PlateDetector(ABC):
    @abstractmethod
    def detect(self, frame: Any, vehicle: VehicleDetection | None = None) -> list[PlateDetection]:
        """Detect number plates, optionally within a vehicle crop."""


class OCRProvider(ABC):
    @abstractmethod
    def read(self, plate_crop: Any) -> OCRResult:
        """Read plate text from a cropped plate image."""
