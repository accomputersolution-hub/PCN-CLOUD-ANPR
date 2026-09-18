from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from pcn_anpr.config import ANPRSettings, clear_anpr_settings_cache
from pcn_anpr.normalize import is_non_plate_text, matches_indian_plate, stitch_plate_fragments
from pcn_anpr.plate_quality import assess_plate_crop


TESTDATA = Path(__file__).resolve().parents[1] / "testdata"


def _pipe():
    clear_anpr_settings_cache()
    from pcn_anpr.factory import build_pipeline

    return build_pipeline(ANPRSettings(provider_mode="real", plate_detector="opencv"))


def test_short_ocr_iu_is_non_plate() -> None:
    assert is_non_plate_text("IU")
    assert is_non_plate_text("iu")
    assert is_non_plate_text("8")
    assert not is_non_plate_text("MH12AB5687")


def test_reject_red_taillight_crop() -> None:
    crop = np.zeros((50, 50, 3), dtype=np.uint8)
    cv2.circle(crop, (25, 25), 20, (15, 15, 230), -1)
    cv2.circle(crop, (25, 25), 8, (200, 200, 255), -1)
    a = assess_plate_crop(crop)
    assert a.reject is True
    assert any("taillight" in r or "few_char" in r or "compact_light" in r for r in a.reasons)


def test_reject_bright_reflective_lamp() -> None:
    crop = np.full((40, 70, 3), 220, dtype=np.uint8)
    a = assess_plate_crop(crop)
    assert a.reject is True


def test_twoline_stitch_mh12_ab5687() -> None:
    assert stitch_plate_fragments("MH 12", "AB 5687") == "MH12AB5687"
    assert matches_indian_plate("MH12AB5687")


def test_best_plate_rejects_iu_false_positive() -> None:
    import sys

    backend = Path(__file__).resolve().parents[2] / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    from app.services import manual_anpr as manual_svc

    result = {
        "plates": [
            {
                "raw_text": "IU",
                "normalized_text": None,
                "matches_pattern": False,
                "ocr_confident": False,
                "non_plate_text": True,
                "ocr_confidence": 0.99,
                "plate_confidence": 0.82,
                "confidence": 0.12,
                "bbox": [10, 10, 50, 40],
            }
        ]
    }
    assert manual_svc.best_plate(result) is None
    # Pattern plate on primary still wins over IU noise.
    result["plates"].insert(
        0,
        {
            "raw_text": "MH12AB5687",
            "normalized_text": "MH12AB5687",
            "matches_pattern": True,
            "ocr_confident": True,
            "non_plate_text": False,
            "on_primary_vehicle": True,
            "vehicle_containment": 0.9,
            "vehicle_iou": 0.5,
            "ocr_confidence": 0.9,
            "plate_confidence": 0.7,
            "confidence": 0.8,
            "bbox": [100, 100, 200, 150],
        },
    )
    best = manual_svc.best_plate(result)
    assert best is not None
    assert best["normalized_text"] == "MH12AB5687"


def test_night_primary_motorcycle_recovers_mh12ab5687() -> None:
    """Exact night CCTV fixture: two-line MH12 / AB5687 on primary motorcycle."""
    path = TESTDATA / "MH12AB5687_night_primary.jpg"
    if not path.is_file():
        pytest.skip("night primary motorcycle fixture missing")
    pipe = _pipe()
    pipe.warm_up()
    result = pipe.process_image(path)
    assert result.get("plate_detected") is True
    plates = result.get("plates") or []
    assert plates, "expected at least one plate candidate"
    best = next((p for p in plates if p.get("matches_pattern") and not p.get("non_plate_text")), None)
    assert best is not None
    assert best.get("normalized_text") == "MH12AB5687"
    # Must never finalize IU / taillight OCR.
    assert best.get("normalized_text") != "IU"
    for p in plates:
        if p.get("matches_pattern"):
            assert (p.get("normalized_text") or "") != "IU"


def test_night_multi_vehicle_prefers_mh12ab5687_or_no_false_iu() -> None:
    path = TESTDATA / "MH12AB5687_night_multi.jpg"
    if not path.is_file():
        pytest.skip("night multi-vehicle fixture missing")
    pipe = _pipe()
    pipe.warm_up()
    result = pipe.process_image(path)
    plates = result.get("plates") or []
    timing = result.get("timing") or {}
    assert result.get("plate_detected") is True, (
        f"expected primary plate; error={result.get('error')} "
        f"vehicles={result.get('vehicles')} timing={timing}"
    )
    best = next((p for p in plates if p.get("matches_pattern") and not p.get("non_plate_text")), None)
    assert best is not None
    assert best.get("normalized_text") == "MH12AB5687"
    assert best.get("normalized_text") not in {"IU", "MH14KX7023"}
    assert plates[0].get("normalized_text") == "MH12AB5687"
    # Stage-1 must not burn dozens of calls — primary reserved budget must finish.
    stage1 = int(timing.get("stage1_calls") or (result.get("ocr_perf") or {}).get("stage1_calls") or 999)
    assert stage1 <= 10, f"stage1 burned too many OCR calls: {stage1}"
    total = int(timing.get("final_ocr_call_count") or 999)
    assert total < 30, f"expected far fewer than 56 OCR calls, got {total}"
    assert timing.get("primary_reserved_budget") is not None
    vehicles = result.get("vehicles") or []
    assert any(v.get("is_primary") for v in vehicles)
    for p in plates:
        if p.get("matches_pattern"):
            assert (p.get("normalized_text") or "") != "IU"
