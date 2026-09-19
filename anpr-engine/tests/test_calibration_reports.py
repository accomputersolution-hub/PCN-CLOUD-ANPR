"""Focused real-image calibration reports + ROI regression companion.

Runs the unchanged pipeline on a few fixtures when available, then scores
via ``build_calibration_report``. Skips gracefully if models/fixtures missing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

TESTDATA = Path(__file__).resolve().parents[1] / "testdata"

# label, filename, optional roi
CASES = [
    ("ready_scene", "BH_22BH6517TA_scene.jpg", None),
    ("night", "MH02EF4187_night.jpg", None),
    ("small_res", "MH20DV2366_320x240.jpg", None),
    ("multi_roi_on", "MH12AB5687_multi_vehicle.jpg", {"enabled": True, "x": 0.15, "y": 0.35, "w": 0.7, "h": 0.55}),
    ("multi_roi_off", "MH12AB5687_multi_vehicle.jpg", None),
]


def _pipeline():
    try:
        from pcn_anpr.factory import build_pipeline

        return build_pipeline()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"pipeline unavailable: {exc}")


@pytest.mark.parametrize("label,name,roi", CASES, ids=[c[0] for c in CASES])
def test_calibration_report_on_fixture(label: str, name: str, roi: dict | None):
    path = TESTDATA / name
    if not path.is_file():
        pytest.skip(f"fixture missing: {name}")

    from pcn_anpr.calibration import build_calibration_report
    from pcn_anpr.image_io import load_bgr

    pipe = _pipeline()
    result = pipe.process_image(path, anpr_roi=roi)
    frame = load_bgr(path)
    report = build_calibration_report(result, frame_bgr=frame, anpr_roi=roi, camera_id=f"test-{label}")

    m = report["metrics"]
    print(
        f"\n[CAL REPORT] {label:16s} status={report['status']:6s} "
        f"plate={m.get('plate_width_px')}x{m.get('plate_height_px')} "
        f"ocr={m.get('plate_text')!r} bright={m.get('brightness')} "
        f"sharp={m.get('sharpness')} roi={m.get('roi_enabled')} "
        f"veh_in_roi={m.get('vehicle_in_roi')} ms={m.get('processing_ms')} "
        f"reasons={report.get('reasons')}"
    )

    assert report["status"] in {"GREEN", "YELLOW", "RED"}
    assert "component_scores" in report
    assert isinstance(report["metrics"]["plate_width_px"], (int, float))
    if roi and roi.get("enabled"):
        assert report["metrics"]["roi_enabled"] is True


def test_roi_unit_suite_still_imported():
    """Regression: ROI helpers remain importable and behave for calibration reuse."""
    from pcn_anpr.anpr_roi import normalize_roi, vehicle_in_anpr_roi, roi_to_xyxy

    n = normalize_roi({"enabled": True, "x": 0.1, "y": 0.2, "w": 0.5, "h": 0.4})
    assert n is not None
    rxy = roi_to_xyxy(n, (1000, 1000))
    assert vehicle_in_anpr_roi([200, 300, 400, 500], rxy) is True
