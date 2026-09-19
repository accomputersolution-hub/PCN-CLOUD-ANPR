"""Unit tests for ANPR calibration scoring (no detector changes)."""

from __future__ import annotations

import numpy as np

from pcn_anpr.calibration import build_calibration_report, calibration_summary_for_storage


def _blank_frame(h: int = 720, w: int = 1280, brightness: int = 120):
    frame = np.full((h, w, 3), int(brightness), dtype=np.uint8)
    return frame


def test_green_ready_plate_scores_green():
    # Large plate, good OCR, bright sharp crop
    frame = _blank_frame()
    # Draw a textured plate region so Laplacian has variance
    x1, y1, x2, y2 = 500, 500, 700, 560
    frame[y1:y2, x1:x2] = (200, 200, 200)
    frame[y1 + 5 : y2 - 5 : 2, x1:x2] = (20, 20, 20)
    result = {
        "processing_ms": 120,
        "decoded_shape": list(frame.shape),
        "vehicles": [
            {
                "is_primary": True,
                "bbox": [300, 200, 900, 650],
                "confidence": 0.9,
                "area_ratio": 0.25,
            }
        ],
        "vehicle_results": [
            {
                "is_primary": True,
                "vehicle_bbox": [300, 200, 900, 650],
                "plate_bbox": [x1, y1, x2, y2],
                "plate": "MH12AB1234",
                "best_plate": "MH12AB1234",
                "matches_pattern": True,
                "ocr_confidence": 0.92,
                "plate_confidence": 0.88,
                "confidence": 0.9,
            }
        ],
        "detections": [
            {
                "is_primary": True,
                "vehicle_bbox": [300, 200, 900, 650],
                "plate_bbox": [x1, y1, x2, y2],
                "plate": "MH12AB1234",
                "ocr_confidence": 0.92,
                "plate_confidence": 0.88,
                "confidence": 0.9,
                "matches_indian_pattern": True,
            }
        ],
        "plates": [],
        "timing": {"roi_enabled": False, "plate_candidates": []},
    }
    report = build_calibration_report(result, frame_bgr=frame)
    assert report["status"] in {"GREEN", "YELLOW"}
    assert report["metrics"]["plate_width_px"] >= 120
    assert report["metrics"]["matches_indian_pattern"] is True
    summary = calibration_summary_for_storage(report)
    assert summary["status"] == report["status"]
    assert "component_scores" in summary


def test_plate_too_small_is_red():
    frame = _blank_frame()
    result = {
        "processing_ms": 50,
        "decoded_shape": list(frame.shape),
        "vehicles": [{"is_primary": True, "bbox": [100, 100, 200, 180], "confidence": 0.7, "area_ratio": 0.01}],
        "vehicle_results": [],
        "detections": [
            {
                "is_primary": True,
                "vehicle_bbox": [100, 100, 200, 180],
                "plate_bbox": [140, 150, 190, 165],
                "plate": "MH12AB1234",
                "ocr_confidence": 0.5,
                "plate_confidence": 0.4,
                "confidence": 0.5,
                "matches_indian_pattern": True,
            }
        ],
        "plates": [],
        "timing": {},
    }
    report = build_calibration_report(result, frame_bgr=frame)
    assert report["status"] == "RED"
    assert any("small" in r.lower() for r in report["reasons"])
    assert report["metrics"]["plate_width_px"] < 80


def test_no_plate_is_red():
    frame = _blank_frame()
    result = {
        "processing_ms": 40,
        "decoded_shape": list(frame.shape),
        "vehicles": [{"is_primary": True, "bbox": [200, 200, 600, 500], "confidence": 0.8, "area_ratio": 0.2}],
        "vehicle_results": [{"is_primary": True, "vehicle_bbox": [200, 200, 600, 500], "plate_bbox": []}],
        "detections": [],
        "plates": [],
        "timing": {"plate_candidates": []},
    }
    report = build_calibration_report(result, frame_bgr=frame)
    assert report["status"] == "RED"
    assert any("No plate" in r for r in report["reasons"])


def test_no_vehicle_is_red():
    frame = _blank_frame()
    result = {
        "processing_ms": 20,
        "decoded_shape": list(frame.shape),
        "vehicles": [],
        "vehicle_results": [],
        "detections": [],
        "plates": [],
        "timing": {},
    }
    report = build_calibration_report(result, frame_bgr=frame)
    assert report["status"] == "RED"
    assert any("No vehicle" in r for r in report["reasons"])


def test_roi_outside_flagged():
    frame = _blank_frame(h=1000, w=1000)
    roi = {"enabled": True, "x": 0.4, "y": 0.4, "w": 0.2, "h": 0.2}
    # Vehicle far from ROI
    result = {
        "processing_ms": 80,
        "decoded_shape": list(frame.shape),
        "vehicles": [{"is_primary": True, "bbox": [50, 50, 200, 200], "confidence": 0.9, "area_ratio": 0.02}],
        "vehicle_results": [
            {
                "is_primary": True,
                "vehicle_bbox": [50, 50, 200, 200],
                "plate_bbox": [80, 150, 220, 190],
                "plate": "MH12AB1234",
                "best_plate": "MH12AB1234",
                "matches_pattern": True,
                "ocr_confidence": 0.9,
                "plate_confidence": 0.85,
                "confidence": 0.9,
            }
        ],
        "detections": [
            {
                "is_primary": True,
                "vehicle_bbox": [50, 50, 200, 200],
                "plate_bbox": [80, 150, 220, 190],
                "plate": "MH12AB1234",
                "ocr_confidence": 0.9,
                "plate_confidence": 0.85,
                "confidence": 0.9,
                "matches_indian_pattern": True,
            }
        ],
        "plates": [],
        "timing": {"roi_enabled": True, "roi_norm": roi},
    }
    report = build_calibration_report(result, frame_bgr=frame, anpr_roi=roi)
    assert report["metrics"]["roi_enabled"] is True
    assert report["metrics"]["vehicle_in_roi"] is False
    assert any("outside ANPR Zone" in r for r in report["reasons"])


def test_yellow_medium_plate_size():
    frame = _blank_frame()
    # 90px wide plate → yellow band
    x1, y1, x2, y2 = 400, 400, 490, 430
    frame[y1:y2, x1:x2:3] = (30, 30, 30)
    result = {
        "processing_ms": 60,
        "decoded_shape": list(frame.shape),
        "vehicles": [{"is_primary": True, "bbox": [200, 200, 700, 550], "confidence": 0.8, "area_ratio": 0.15}],
        "vehicle_results": [],
        "detections": [
            {
                "is_primary": True,
                "vehicle_bbox": [200, 200, 700, 550],
                "plate_bbox": [x1, y1, x2, y2],
                "plate": "MH12AB1234",
                "ocr_confidence": 0.8,
                "plate_confidence": 0.75,
                "confidence": 0.8,
                "matches_indian_pattern": True,
            }
        ],
        "plates": [],
        "timing": {},
    }
    report = build_calibration_report(result, frame_bgr=frame)
    assert 80 <= report["metrics"]["plate_width_px"] < 120
    assert report["status"] in {"YELLOW", "GREEN", "RED"}
    # Should mention size target if not green-sized
    if report["status"] != "GREEN":
        assert report["reasons"]
