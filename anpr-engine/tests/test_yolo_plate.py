from __future__ import annotations

from pcn_anpr.config import ANPRSettings, clear_anpr_settings_cache
from pcn_anpr.factory import build_plate_detector
from pcn_anpr.opencv_plate import OpenCVPlateDetector


def test_plate_detector_default_opencv() -> None:
    clear_anpr_settings_cache()
    det = build_plate_detector(ANPRSettings(provider_mode="real", plate_detector="opencv"))
    assert isinstance(det, OpenCVPlateDetector)


def test_plate_detector_yolo_mode_constructs_or_falls_back() -> None:
    clear_anpr_settings_cache()
    det = build_plate_detector(
        ANPRSettings(
            provider_mode="real",
            plate_detector="yolo",
            yolo_plate_weights="",  # default path; may download or fallback
        )
    )
    name = type(det).__name__
    assert name in {"YOLOPlateDetector", "OpenCVPlateDetector"}
