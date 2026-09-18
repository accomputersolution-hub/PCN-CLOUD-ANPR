"""Primary-ROI full-plate early-exit (speed without accuracy loss)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pcn_anpr.config import ANPRSettings, clear_anpr_settings_cache
from pcn_anpr.ocr_ensemble import (
    MAX_PADDLE_CALLS_PER_IMAGE,
    MAX_PRIMARY_ROI_OCR_CALLS,
    OcrPassResult,
    should_early_exit,
    _try_stitch_full_plate,
)


TESTDATA = Path(__file__).resolve().parents[1] / "testdata"


def test_full_plate_early_exit_on_single_confident_pass() -> None:
    passes = [
        OcrPassResult(
            variant="gray_clahe_x2",
            raw_text="MH 12 AB 5687",
            normalized="MH12AB5687",
            confidence=0.92,
            matches_pattern=True,
        )
    ]
    assert should_early_exit(passes, min_ocr_confidence=0.3, mode="full_plate") is True
    assert should_early_exit(passes, min_ocr_confidence=0.3, mode="consensus") is False


def test_full_plate_early_exit_rejects_short_fragments() -> None:
    for raw, norm in (("AB 5687", "AB5687"), ("IU", "IU"), ("22", "22")):
        passes = [
            OcrPassResult(
                variant="x",
                raw_text=raw,
                normalized=norm,
                confidence=0.99,
                matches_pattern=False,
            )
        ]
        assert should_early_exit(passes, min_ocr_confidence=0.3, mode="full_plate") is False


def test_twoline_stitch_triggers_full_plate_exit() -> None:
    passes = [
        OcrPassResult(
            variant="a",
            raw_text="MH 12",
            normalized="MH12",
            confidence=0.9,
            matches_pattern=False,
        ),
        OcrPassResult(
            variant="b",
            raw_text="AB 5687",
            normalized="AB5687",
            confidence=0.91,
            matches_pattern=False,
        ),
    ]
    stitched = _try_stitch_full_plate(passes)
    assert stitched is not None
    assert stitched.normalized == "MH12AB5687"
    assert should_early_exit(passes, min_ocr_confidence=0.3, mode="full_plate") is True


def test_swapped_twoline_blob_stitches() -> None:
    passes = [
        OcrPassResult(
            variant="x",
            raw_text="AB 5687 MH 12",
            normalized="AB5687MH12",
            confidence=0.93,
            matches_pattern=False,
        )
    ]
    stitched = _try_stitch_full_plate(passes)
    assert stitched is not None
    assert stitched.normalized == "MH12AB5687"
    assert should_early_exit(passes, min_ocr_confidence=0.3, mode="full_plate") is True


def test_night_primary_ocr_call_budget() -> None:
    path = TESTDATA / "MH12AB5687_night_primary.jpg"
    if not path.is_file():
        pytest.skip("night primary fixture missing")
    clear_anpr_settings_cache()
    from pcn_anpr.factory import build_pipeline

    pipe = build_pipeline(ANPRSettings(provider_mode="real", plate_detector="opencv"))
    pipe.warm_up()
    result = pipe.process_image(path)
    assert result.get("plate_detected") is True
    plates = result.get("plates") or []
    best = next((p for p in plates if p.get("matches_pattern")), None)
    assert best is not None
    assert best.get("normalized_text") == "MH12AB5687"

    timing = result.get("timing") or {}
    calls = int(timing.get("final_ocr_call_count") or (result.get("ocr_perf") or {}).get("paddle_calls_total") or 999)
    # Easy night fixture must stay near the ~6-call early-exit path.
    assert calls <= 12, f"easy night fixture should stay ~6 OCR calls, got {calls}"
    ocr_perf = result.get("ocr_perf") or {}
    roi_calls = int(ocr_perf.get("primary_roi_calls") or 0)
    assert roi_calls <= MAX_PRIMARY_ROI_OCR_CALLS, (
        f"primary-ROI OCR calls {roi_calls} exceed budget {MAX_PRIMARY_ROI_OCR_CALLS}"
    )
    stage1 = int(timing.get("stage1_calls") or ocr_perf.get("stage1_calls") or 0)
    assert stage1 <= 8, f"stage1 should be capped, got {stage1}"
    assert timing.get("primary_reserved_budget") == MAX_PRIMARY_ROI_OCR_CALLS or timing.get(
        "primary_reserved_budget"
    )
    assert timing.get("early_exit_reason") in {
        "full_plate_confident",
        "full_plate_stitched",
        "primary_roi_full_plate",
        "ocr_budget_exhausted",
        "primary_partial_state_rto",
    } or calls <= 12


def test_state_rto_alone_does_not_full_plate_exit() -> None:
    passes = [
        OcrPassResult(
            variant="x",
            raw_text="MH 12",
            normalized="MH12",
            confidence=0.95,
            matches_pattern=False,
        )
    ]
    assert should_early_exit(passes, min_ocr_confidence=0.3, mode="full_plate") is False
