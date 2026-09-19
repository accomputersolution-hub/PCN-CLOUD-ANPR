"""Unit tests for camera ANPR zone filtering (no detector changes)."""

from __future__ import annotations

from types import SimpleNamespace

from pcn_anpr.anpr_roi import (
    filter_vehicles_by_anpr_roi,
    normalize_roi,
    roi_to_xyxy,
    vehicle_in_anpr_roi,
)


def _veh(x: float, y: float, w: float, h: float, conf: float = 0.9, label: str = "car"):
    return SimpleNamespace(
        bbox=SimpleNamespace(x=x, y=y, w=w, h=h),
        confidence=conf,
        label=label,
    )


def test_normalize_roi_disabled_when_missing_or_flag():
    assert normalize_roi(None) is None
    assert normalize_roi({"enabled": False, "x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}) is None
    n = normalize_roi({"enabled": True, "x": 0.1, "y": 0.2, "w": 0.4, "h": 0.3})
    assert n is not None
    assert n["x"] == 0.1 and n["w"] == 0.4


def test_bottom_center_inside_keeps_vehicle():
    # Frame 1000x1000; ROI lower half; vehicle standing in gate
    roi = roi_to_xyxy({"x": 0.2, "y": 0.4, "w": 0.6, "h": 0.5}, (1000, 1000))
    # bottom-center at (500, 800) inside ROI
    assert vehicle_in_anpr_roi([400, 500, 600, 800], roi)


def test_bottom_center_outside_but_overlap_keeps():
    roi = roi_to_xyxy({"x": 0.4, "y": 0.4, "w": 0.2, "h": 0.2}, (1000, 1000))
    # Large vehicle mostly outside but overlaps ROI significantly
    assert vehicle_in_anpr_roi([100, 100, 700, 700], roi)


def test_fully_outside_rejected():
    roi = roi_to_xyxy({"x": 0.7, "y": 0.7, "w": 0.2, "h": 0.2}, (1000, 1000))
    assert not vehicle_in_anpr_roi([10, 10, 100, 100], roi)


def test_filter_noop_without_roi():
    vehicles = [_veh(10, 10, 50, 50), _veh(200, 200, 50, 50)]
    kept, stats = filter_vehicles_by_anpr_roi(
        vehicles, anpr_roi=None, frame_shape=(500, 500)
    )
    assert len(kept) == 2
    assert stats["roi_enabled"] is False
    assert stats["ignored_outside_roi"] == 0


def test_filter_keeps_only_roi_vehicles():
    # Frame 1000x800; gate ROI center-bottom
    roi = {"enabled": True, "x": 0.25, "y": 0.35, "w": 0.5, "h": 0.55}
    gate_car = _veh(300, 300, 200, 350)  # bottom-center ~400,650 in zone
    distant = _veh(50, 20, 80, 60)  # top-left far
    kept, stats = filter_vehicles_by_anpr_roi(
        [gate_car, distant], anpr_roi=roi, frame_shape=(800, 1000)
    )
    assert stats["roi_enabled"] is True
    assert stats["total_yolo_vehicles"] == 2
    assert len(kept) == 1
    assert stats["ignored_outside_roi"] == 1
    assert kept[0] is gate_car
