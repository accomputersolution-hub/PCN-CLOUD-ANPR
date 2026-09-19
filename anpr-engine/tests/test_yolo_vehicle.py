from __future__ import annotations

"""Factory + YOLO vehicle detector unit tests (mockable; no GPU required)."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from pcn_anpr.config import ANPRSettings, clear_anpr_settings_cache
from pcn_anpr.factory import NoOpVehicleDetector, build_vehicle_detector
from pcn_anpr.interfaces import BoundingBox, PlateDetection, VehicleDetection
from pcn_anpr.mock_providers import MockVehicleDetector
from pcn_anpr.opencv_vehicle import OpenCVVehicleDetector
from pcn_anpr.vehicle_assoc import synthesize_vehicles_from_plates


@pytest.fixture(autouse=True)
def _clear_settings():
    clear_anpr_settings_cache()
    yield
    clear_anpr_settings_cache()


def test_factory_mock_mode() -> None:
    det = build_vehicle_detector(ANPRSettings(provider_mode="mock", vehicle_detector="yolo"))
    assert isinstance(det, MockVehicleDetector)


def test_factory_opencv_explicit() -> None:
    det = build_vehicle_detector(ANPRSettings(provider_mode="real", vehicle_detector="opencv"))
    assert isinstance(det, OpenCVVehicleDetector)
    assert det.detector_name == "opencv"


def test_factory_disabled() -> None:
    det = build_vehicle_detector(
        ANPRSettings(provider_mode="real", vehicle_detector="yolo", vehicle_detector_enabled=False)
    )
    assert isinstance(det, NoOpVehicleDetector)


def test_factory_yolo_when_ultralytics_present() -> None:
    with patch("pcn_anpr.factory._yolo_available", return_value=True):
        with patch("pcn_anpr.yolo_vehicle.YOLOVehicleDetector") as cls:
            cls.return_value = MagicMock(detector_name="yolo")
            det = build_vehicle_detector(ANPRSettings(provider_mode="real", vehicle_detector="yolo"))
            cls.assert_called_once()
            assert det.detector_name == "yolo"


def test_factory_yolo_falls_back_without_ultralytics() -> None:
    with patch("pcn_anpr.factory._yolo_available", return_value=False):
        det = build_vehicle_detector(ANPRSettings(provider_mode="real", vehicle_detector="yolo"))
        assert isinstance(det, OpenCVVehicleDetector)


def test_yolo_detector_maps_coco_boxes() -> None:
    from pcn_anpr.yolo_vehicle import YOLOVehicleDetector

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    det = YOLOVehicleDetector(weights="yolov8n.pt", device="cpu")

    box = MagicMock()
    box.cls = [MagicMock(item=lambda: 2)]  # car
    box.conf = [MagicMock(item=lambda: 0.91)]
    box.xyxy = [MagicMock(tolist=lambda: [100.0, 120.0, 300.0, 280.0])]

    result = MagicMock()
    result.boxes = [box]
    model = MagicMock()
    model.predict.return_value = [result]
    det._model = model
    det._device = "cpu"

    out = det.detect(frame)
    assert len(out) == 1
    assert out[0].label == "car"
    assert out[0].confidence == pytest.approx(0.91, abs=1e-3)
    assert out[0].bbox.x == pytest.approx(100.0)
    assert out[0].bbox.w == pytest.approx(200.0)


def test_yolo_returns_empty_not_full_frame() -> None:
    from pcn_anpr.yolo_vehicle import YOLOVehicleDetector

    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    det = YOLOVehicleDetector(device="cpu")
    model = MagicMock()
    empty = MagicMock()
    empty.boxes = []
    model.predict.return_value = [empty]
    det._model = model
    det._device = "cpu"
    assert det.detect(frame) == []
    assert det.full_frame_fallback is False


def test_synth_skips_hosted_plate_when_yolo_car_present() -> None:
    car = VehicleDetection(
        bbox=BoundingBox(50, 80, 400, 300, 0.9),
        label="car",
        confidence=0.9,
    )
    plate = PlateDetection(BoundingBox(180, 300, 120, 40, 0.8), confidence=0.8)
    vehicles, meta = synthesize_vehicles_from_plates(
        [plate],
        frame_shape=(480, 640),
        existing=[car],
        prefer_detector_hosts=True,
    )
    assert meta["prefer_detector_hosts"] is True
    assert meta["synthesized"] == 0
    assert meta["final_detector"] == 1
    assert len(vehicles) == 1
    assert vehicles[0].label == "car"


def test_synth_keeps_orphan_yolo_when_other_plate_needs_host() -> None:
    """YOLO car without a plate must not be dropped when another plate synthesizes."""
    car = VehicleDetection(
        bbox=BoundingBox(20, 40, 200, 160, 0.85),
        label="car",
        confidence=0.85,
    )
    # Plate far from the car → needs a synthetic host.
    plate = PlateDetection(BoundingBox(450, 300, 140, 45, 0.8), confidence=0.8)
    vehicles, meta = synthesize_vehicles_from_plates(
        [plate],
        frame_shape=(480, 640),
        existing=[car],
        prefer_detector_hosts=True,
    )
    assert meta.get("dropped_orphan_detector", 0) == 0
    labels = {v.label for v in vehicles}
    assert "car" in labels
    assert "vehicle_from_plate" in labels
