from __future__ import annotations

"""Live / fixture smoke for TN+alamy, MH20, and BH pattern ranking."""

from pathlib import Path

import pytest

from pcn_anpr.normalize import is_non_plate_text, matches_indian_plate


TESTDATA = Path(__file__).resolve().parents[1] / "testdata"


def _best(result: dict) -> dict | None:
    plates = list(result.get("plates") or [])
    if not plates:
        return None
    return plates[0]


@pytest.mark.slow
def test_tn51y6552_alamy_image_prefers_plate() -> None:
    path = TESTDATA / "TN51Y6552_alamy.jpg"
    if not path.is_file():
        pytest.skip("TN51Y6552 alamy fixture missing")
    from pcn_anpr.config import ANPRSettings
    from pcn_anpr.factory import build_pipeline

    pipe = build_pipeline(ANPRSettings(provider_mode="real", plate_detector="opencv", vehicle_detector="opencv"))
    result = pipe.process_image(path)
    best = _best(result)
    assert best is not None, f"no plates: {result}"
    norm = best.get("normalized_text")
    raw = (best.get("raw_text") or "").lower()
    assert "alamy" not in raw or best.get("non_plate_text") is True
    assert not is_non_plate_text(norm or "")
    assert norm == "TN51Y6552", (
        f"expected TN51Y6552, got raw={best.get('raw_text')!r} norm={norm!r} "
        f"conf={best.get('ocr_confidence')} passes={best.get('ocr_passes')}"
    )
    assert best.get("matches_pattern") is True
    assert best.get("ocr_confident") is True
    assert matches_indian_plate(norm)


@pytest.mark.slow
def test_mh20dv2366_car_still_reads() -> None:
    path = TESTDATA / "MH20DV2366_car.jpg"
    if not path.is_file():
        pytest.skip("MH20 car fixture missing")
    from pcn_anpr.config import ANPRSettings
    from pcn_anpr.factory import build_pipeline

    pipe = build_pipeline(ANPRSettings(provider_mode="real", plate_detector="opencv", vehicle_detector="opencv"))
    result = pipe.process_image(path)
    best = _best(result)
    assert best is not None
    assert best.get("normalized_text") == "MH20DV2366"
    assert best.get("ocr_confident") is True


@pytest.mark.slow
def test_mh20dv2366_320x240_still_reads() -> None:
    path = TESTDATA / "MH20DV2366_320x240.jpg"
    if not path.is_file():
        pytest.skip("MH20 320x240 fixture missing")
    from pcn_anpr.config import ANPRSettings
    from pcn_anpr.factory import build_pipeline

    pipe = build_pipeline(ANPRSettings(provider_mode="real", plate_detector="opencv", vehicle_detector="opencv"))
    result = pipe.process_image(path)
    best = _best(result)
    assert best is not None
    assert best.get("normalized_text") == "MH20DV2366"
    assert best.get("ocr_confident") is True
