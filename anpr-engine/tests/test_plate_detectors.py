from __future__ import annotations

"""Unit tests for plate-detector abstraction, AI placeholder, and mode selection."""

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from pcn_anpr.ai_plate import AILicensePlateDetector
from pcn_anpr.config import ANPRSettings, clear_anpr_settings_cache, get_anpr_settings
from pcn_anpr.factory import build_plate_detector
from pcn_anpr.interfaces import BoundingBox, PlateDetection, PlateDetector, VehicleDetection
from pcn_anpr.opencv_plate import OpenCVPlateDetector
from pcn_anpr.plate_modes import AIWithOpenCVFallback, ComparePlateDetector


class _FixedDetector(PlateDetector):
    def __init__(self, plates: list[PlateDetection], *, name: str = "fixed") -> None:
        self.plates = plates
        self.name = name
        self.calls = 0

    def detect(self, frame: Any, vehicle: VehicleDetection | None = None) -> list[PlateDetection]:
        self.calls += 1
        return list(self.plates)


def _box(conf: float = 0.9) -> PlateDetection:
    return PlateDetection(
        bbox=BoundingBox(10.0, 20.0, 100.0, 30.0, conf),
        confidence=conf,
        class_name="license_plate",
    )


@pytest.fixture(autouse=True)
def _clear_settings() -> None:
    clear_anpr_settings_cache()
    yield
    clear_anpr_settings_cache()


def test_settings_default_plate_detector_opencv() -> None:
    s = ANPRSettings()
    assert s.plate_detector == "opencv"


def test_settings_from_env_plate_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANPR_PLATE_DETECTOR", "compare")
    monkeypatch.setenv("ANPR_AI_PLATE_MODEL_DIR", "/tmp/does-not-exist-ai-plate")
    clear_anpr_settings_cache()
    s = get_anpr_settings()
    assert s.plate_detector == "compare"
    assert s.ai_plate_model_dir.endswith("does-not-exist-ai-plate")


def test_settings_invalid_plate_detector_falls_back_opencv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANPR_PLATE_DETECTOR", "yolo")
    clear_anpr_settings_cache()
    assert get_anpr_settings().plate_detector == "opencv"


def test_factory_default_is_opencv() -> None:
    det = build_plate_detector(ANPRSettings(provider_mode="real", plate_detector="opencv"))
    assert isinstance(det, OpenCVPlateDetector)


def test_factory_ai_mode_wraps_fallback() -> None:
    det = build_plate_detector(ANPRSettings(provider_mode="real", plate_detector="ai"))
    assert isinstance(det, AIWithOpenCVFallback)


def test_factory_compare_mode() -> None:
    det = build_plate_detector(ANPRSettings(provider_mode="real", plate_detector="compare"))
    assert isinstance(det, ComparePlateDetector)


def test_factory_mock_ignores_plate_mode() -> None:
    from pcn_anpr.mock_providers import MockPlateDetector

    det = build_plate_detector(ANPRSettings(provider_mode="mock", plate_detector="ai"))
    assert isinstance(det, MockPlateDetector)


def test_opencv_adapter_returns_plate_detections() -> None:
    det = OpenCVPlateDetector(min_confidence=0.1)
    frame = np.zeros((240, 320, 3), dtype=np.uint8)
    # Synthetic high-contrast plate-like rectangle
    frame[160:190, 40:280] = 240
    import cv2

    cv2.rectangle(frame, (40, 160), (280, 190), (0, 0, 0), 2)
    dets = det.detect(frame, None)
    assert isinstance(dets, list)
    for d in dets:
        assert isinstance(d, PlateDetection)
        assert d.bbox.w > 0 and d.bbox.h > 0
        assert d.confidence >= 0.0
        assert d.class_name == "license_plate" or d.class_name is None or isinstance(d.class_name, str)


def test_ai_missing_model_returns_empty() -> None:
    ai = AILicensePlateDetector(model_dir="", eager=False)
    frame = np.zeros((96, 192, 3), dtype=np.uint8)
    assert ai.detect(frame, None) == []
    assert ai.init_error
    assert ai.path_configured is False
    # Second call still empty; error logged once (no crash)
    assert ai.detect(frame, None) == []


def test_ai_invalid_model_dir_returns_empty(tmp_path) -> None:
    empty = tmp_path / "empty_model"
    empty.mkdir()
    ai = AILicensePlateDetector(model_dir=str(empty))
    assert ai.detect(np.zeros((64, 128, 3), dtype=np.uint8), None) == []
    assert ai.init_error


def test_ai_invalid_bbox_filtered() -> None:
    ai = AILicensePlateDetector(model_dir="")

    # Inject a fake predictor path by monkeypatching internals
    ai._path_configured = True
    ai._init_error = None
    ai._predictor = object()

    def _bad_infer(predictor, frame, vehicle):
        return [
            {"bbox": [10, 10, 5, 5], "confidence": 0.99},  # inverted / tiny
            {"bbox": [0, 0, 80, 24], "confidence": 0.05},  # low conf
            {"bbox": [5, 5, 100, 40], "confidence": 0.92},  # good
            {"bbox": "nope", "confidence": 0.9},  # bad shape
        ]

    ai._run_inference = _bad_infer  # type: ignore[method-assign]
    ai.min_confidence = 0.25
    frame = np.zeros((120, 200, 3), dtype=np.uint8)
    dets = ai.detect(frame, None)
    assert len(dets) == 1
    assert dets[0].confidence == pytest.approx(0.92)
    assert dets[0].class_name == "license_plate"
    assert dets[0].timing_ms is not None
    assert dets[0].bbox.w == pytest.approx(95.0)
    assert dets[0].bbox.h == pytest.approx(35.0)


def test_ai_warmup_does_not_crash() -> None:
    ai = AILicensePlateDetector(model_dir="")
    info = ai.warm_up()
    assert "warmup_ms" in info
    assert info.get("dummy_detections") == 0


def test_compare_runs_both_uses_opencv() -> None:
    cv_plate = _box(0.88)
    ai_plate = PlateDetection(
        bbox=BoundingBox(1.0, 2.0, 50.0, 15.0, 0.99),
        confidence=0.99,
        class_name="license_plate",
    )
    opencv = _FixedDetector([cv_plate], name="opencv")
    ai = _FixedDetector([ai_plate], name="ai")
    cmp = ComparePlateDetector(opencv, ai)
    frame = np.zeros((80, 160, 3), dtype=np.uint8)
    out = cmp.detect(frame, None)
    assert opencv.calls == 1
    assert ai.calls == 1
    assert len(out) == 1
    assert out[0].confidence == pytest.approx(0.88)
    assert cmp.last_meta["used"] == "opencv"
    assert cmp.last_meta["opencv_count"] == 1
    assert cmp.last_meta["ai_count"] == 1


def test_ai_mode_falls_back_to_opencv() -> None:
    cv_plate = _box(0.7)
    opencv = _FixedDetector([cv_plate], name="opencv")
    ai = _FixedDetector([], name="ai")
    wrapped = AIWithOpenCVFallback(ai, opencv)
    out = wrapped.detect(np.zeros((40, 80, 3), dtype=np.uint8), None)
    assert len(out) == 1
    assert out[0].confidence == pytest.approx(0.7)
    assert wrapped.last_meta["used"] == "opencv_fallback"


def test_ai_mode_prefers_ai_when_present() -> None:
    cv_plate = _box(0.7)
    ai_plate = _box(0.95)
    opencv = _FixedDetector([cv_plate], name="opencv")
    ai = _FixedDetector([ai_plate], name="ai")
    wrapped = AIWithOpenCVFallback(ai, opencv)
    out = wrapped.detect(np.zeros((40, 80, 3), dtype=np.uint8), None)
    assert len(out) == 1
    assert out[0].confidence == pytest.approx(0.95)
    assert wrapped.last_meta["used"] == "ai"
    assert opencv.calls == 0


def test_plate_detection_backward_compatible_fields() -> None:
    p = PlateDetection(bbox=BoundingBox(0, 0, 10, 5, 0.5), confidence=0.5)
    assert p.class_name == "license_plate"
    assert p.timing_ms is None
