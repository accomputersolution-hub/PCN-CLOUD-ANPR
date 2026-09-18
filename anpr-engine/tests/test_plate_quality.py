from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pcn_anpr.plate_quality import assess_plate_crop, count_char_like_blobs


def _plate_like_crop() -> np.ndarray:
    """Synthetic white plate with dark glyph-like blobs."""
    img = np.full((48, 220, 3), 210, dtype=np.uint8)
    # Dark vertical bars ≈ characters
    for x in (20, 45, 70, 95, 120, 145, 170, 195):
        img[10:40, x : x + 12] = 25
    return img


def _road_crop() -> np.ndarray:
    rng = np.random.default_rng(0)
    noise = rng.integers(80, 200, size=(40, 220, 3), dtype=np.uint8)
    return noise


def test_char_blobs_on_synthetic_plate() -> None:
    import cv2

    gray = cv2.cvtColor(_plate_like_crop(), cv2.COLOR_BGR2GRAY)
    assert count_char_like_blobs(gray) >= 4


def test_reject_road_texture() -> None:
    road = _road_crop()
    assessment = assess_plate_crop(road, bbox_xyxy=(100, 900, 320, 940), frame_shape=(1000, 1200))
    assert assessment.reject is True
    assert assessment.char_blobs < 2 or any("bottom_road" in r or "char_blobs" in r or "chaotic" in r for r in assessment.reasons)


def test_accept_glyph_rich_plate() -> None:
    plate = _plate_like_crop()
    assessment = assess_plate_crop(plate, bbox_xyxy=(200, 500, 420, 548), frame_shape=(1000, 1200))
    assert assessment.reject is False
    assert assessment.char_blobs >= 4
    assert assessment.score > 0.35


@pytest.mark.skipif(
    not Path("testdata/MH12AB1234_oblique.jpg").is_file(),
    reason="oblique MH12 fixture missing",
)
def test_oblique_mh12_detector_finds_plate_not_road() -> None:
    from pcn_anpr.image_io import load_bgr
    from pcn_anpr.opencv_plate import OpenCVPlateDetector
    from pcn_anpr.opencv_vehicle import OpenCVVehicleDetector

    frame = load_bgr(Path("testdata/MH12AB1234_oblique.jpg"))
    fh = frame.shape[0]
    vehicles = OpenCVVehicleDetector().detect(frame)
    dets = OpenCVPlateDetector(min_confidence=0.2).detect(frame, vehicles[0] if vehicles else None)
    assert dets, "expected at least one plate candidate"
    best = dets[0]
    cy = (best.bbox.y + best.bbox.h * 0.5) / fh
    assert cy < 0.88, f"top candidate still in road zone cy={cy:.2f} bbox={best.bbox}"
    # Plate is near bumper ~y 480-580
    assert best.bbox.y < fh * 0.75
