"""Replaceable ANPR engine package.

Default ``ANPRPipeline()`` still uses mock providers (backward compatible).
For real inference use ``pcn_anpr.factory.build_pipeline()`` or the CLI.

Licenses (Phase 6A defaults):
- OpenCV vehicle/plate heuristics: Apache 2.0 (opencv-python-headless)
- PaddleOCR: Apache 2.0
- No AGPL Ultralytics/YOLO dependency is included by default
"""

from pcn_anpr.factory import build_pipeline, build_plate_detector
from pcn_anpr.interfaces import OCRProvider, PlateDetector, VehicleDetector
from pcn_anpr.pipeline import ANPRPipeline

__all__ = [
    "ANPRPipeline",
    "OCRProvider",
    "PlateDetector",
    "VehicleDetector",
    "build_pipeline",
    "build_plate_detector",
]
