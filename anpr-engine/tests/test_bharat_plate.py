from __future__ import annotations

from pathlib import Path

import pytest

from pcn_anpr.normalize import (
    is_non_plate_text,
    is_plate_marker_noise,
    matches_indian_plate,
    normalize_plate,
    stitch_plate_fragments,
)
from pcn_anpr.ocr_ensemble import OcrPassResult, select_best_pass


TESTDATA = Path(__file__).resolve().parents[1] / "testdata"


def test_stitch_tn_fragments() -> None:
    assert stitch_plate_fragments("TN 51", "Y 6552") == "TN51Y6552"
    assert stitch_plate_fragments("Y6552", "TN51") == "TN51Y6552"
    assert stitch_plate_fragments("alamy", "TN51", "Y6552") == "TN51Y6552"
    assert stitch_plate_fragments("IND", "22BH6517TA") == "22BH6517TA"
    assert stitch_plate_fragments("22", "BH6517TA") == "22BH6517TA"
    assert stitch_plate_fragments("alamy", "IND") is None


def test_stitch_twoline_motorcycle_fragments() -> None:
    assert stitch_plate_fragments("MH02G", "D7249") == "MH02GD7249"
    assert stitch_plate_fragments("D7249", "MH02G") == "MH02GD7249"
    # Noisy multipass debris must not block pair stitching.
    assert (
        stitch_plate_fragments("D7249", "NO", "MH02G", "LATL", "CROP", "IND")
        == "MH02GD7249"
    )
    # Two-line OCR with IND glued between rows.
    assert normalize_plate("MH02G IND D7249").normalized == "MH02GD7249"
    assert normalize_plate("MH02G IND D7249").matches_known_pattern is True


def test_bh_and_standard_patterns() -> None:
    assert matches_indian_plate("22BH6517TA")
    assert matches_indian_plate("MH20DV2366")
    assert matches_indian_plate("TN51Y6552")
    assert normalize_plate("IND22BH6517TA").normalized == "22BH6517TA"
    assert normalize_plate("TN 51 Y 6552").normalized == "TN51Y6552"


def test_alamy_and_ind_are_non_plate() -> None:
    assert is_non_plate_text("alamy")
    assert is_non_plate_text("ALAMY")
    assert is_non_plate_text("IND")
    assert is_plate_marker_noise("IND")
    assert not is_non_plate_text("TN51Y6552")
    assert not is_non_plate_text("22BH6517TA")
    assert not is_non_plate_text("MH20DV2366")


def test_select_rejects_alamy_for_tn_plate() -> None:
    passes = [
        OcrPassResult("a", "alamy", "ALAMY", 0.9998, False),
        OcrPassResult("b", "TN 51 Y 6552", "TN51Y6552", 0.91, True),
    ]
    best = select_best_pass(passes, min_ocr_confidence=0.3)
    assert best.normalized_text == "TN51Y6552"
    assert best.ocr_confident is True
    assert "alamy" not in (best.raw_text or "").lower()


def test_select_prefers_longer_incomplete_over_repeated_truncation() -> None:
    passes = [
        OcrPassResult("a", "6552", "6552", 1.0, False),
        OcrPassResult("b", "6552", "6552", 1.0, False),
        OcrPassResult("c", "6552", "6552", 1.0, False),
        OcrPassResult("d", "Y6552", "Y6552", 0.999, False),
    ]
    best = select_best_pass(passes, min_ocr_confidence=0.3)
    assert best.raw_text == "Y6552"


def test_select_alamy_alone_not_confident() -> None:
    passes = [OcrPassResult("a", "alamy", "ALAMY", 0.9998, False)]
    best = select_best_pass(passes, min_ocr_confidence=0.3)
    assert best.ocr_confident is False
    assert best.normalized_text is None
    assert is_non_plate_text(best.raw_text)


def test_select_rejects_ind_in_favor_of_bh_plate() -> None:
    passes = [
        OcrPassResult("a", "IND", "IND", 0.999, False),
        OcrPassResult("b", "22 BH 6517 TA", "22BH6517TA", 0.92, True),
    ]
    best = select_best_pass(passes, min_ocr_confidence=0.3)
    assert best.normalized_text == "22BH6517TA"
    assert best.ocr_confident is True
    assert best.raw_text != "IND"


def test_select_ind_alone_not_confident() -> None:
    passes = [OcrPassResult("a", "IND", "IND", 0.999, False)]
    best = select_best_pass(passes, min_ocr_confidence=0.3)
    assert best.ocr_confident is False
    assert best.normalized_text is None
    assert is_plate_marker_noise(best.raw_text)


def test_mh20_still_preferred() -> None:
    passes = [
        OcrPassResult("a", "IND", "IND", 0.99, False),
        OcrPassResult("b", "alamy", "ALAMY", 0.999, False),
        OcrPassResult("c", "MH20DV2366", "MH20DV2366", 0.96, True),
    ]
    best = select_best_pass(passes, min_ocr_confidence=0.3)
    assert best.normalized_text == "MH20DV2366"


def test_pattern_beats_higher_ocr_confidence_noise() -> None:
    """Non-plate text must never win solely on OCR confidence."""
    passes = [
        OcrPassResult("wm", "alamy", "ALAMY", 0.9999, False),
        OcrPassResult("ind", "IND", "IND", 0.9995, False),
        OcrPassResult("plate", "TN51Y6552", "TN51Y6552", 0.55, True),
    ]
    best = select_best_pass(passes, min_ocr_confidence=0.3)
    assert best.normalized_text == "TN51Y6552"
    assert best.matches_pattern is True


def test_synthetic_bh_plate_crop_ocr() -> None:
    path = TESTDATA / "BH_22BH6517TA.jpg"
    if not path.is_file():
        pytest.skip("synthetic BH plate fixture missing")
    import cv2

    from pcn_anpr.config import ANPRSettings
    from pcn_anpr.factory import build_pipeline
    from pcn_anpr.ocr_ensemble import run_multipass_ocr

    img = cv2.imread(str(path))
    assert img is not None
    # Explicit real mode — CLI mock tests may leave ANPR_PROVIDER_MODE=mock in env.
    pipe = build_pipeline(ANPRSettings(provider_mode="real", plate_detector="opencv"))
    pipe.warm_up()
    ens = run_multipass_ocr(
        img,
        pipe.ocr,
        min_ocr_confidence=0.3,
        scales=(2.0, 3.0),
        adaptive_fast_path=True,
    )
    assert ens.normalized_text == "22BH6517TA"
    assert ens.ocr_confident is True
    assert matches_indian_plate(ens.normalized_text or "")


def test_twoline_bullet_recovers_full_plate() -> None:
    path = TESTDATA / "MH02GD7249_twoline_bullet.jpg"
    if not path.is_file():
        pytest.skip("two-line Bullet fixture missing")
    from pcn_anpr.config import ANPRSettings
    from pcn_anpr.factory import build_pipeline

    pipe = build_pipeline(ANPRSettings(provider_mode="real", plate_detector="opencv"))
    pipe.warm_up()
    result = pipe.process_image(path)
    plates = result.get("plates") or []
    assert plates
    best = plates[0]
    assert best.get("normalized_text") == "MH02GD7249"
    assert best.get("matches_pattern") is True
    assert best.get("ocr_confident") is True


def test_synthetic_bh_scene_not_ind_only() -> None:
    path = TESTDATA / "BH_22BH6517TA_scene.jpg"
    if not path.is_file():
        pytest.skip("synthetic BH scene fixture missing")
    from pcn_anpr.config import ANPRSettings
    from pcn_anpr.factory import build_pipeline

    pipe = build_pipeline(ANPRSettings(provider_mode="real", plate_detector="opencv"))
    result = pipe.process_image(path)
    plates = result.get("plates") or []
    assert plates
    best = plates[0]
    norm = best.get("normalized_text")
    assert not is_plate_marker_noise(norm or "")
    assert best.get("marker_noise") is not True or norm is None
    if best.get("ocr_confident"):
        assert norm == "22BH6517TA"
        assert matches_indian_plate(norm)
