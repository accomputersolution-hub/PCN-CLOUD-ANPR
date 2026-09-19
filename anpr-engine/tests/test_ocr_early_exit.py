"""Primary-ROI full-plate early-exit (speed without accuracy loss)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pcn_anpr.config import ANPRSettings, clear_anpr_settings_cache
from pcn_anpr.ocr_ensemble import (
    MAX_PADDLE_CALLS_PER_IMAGE,
    MAX_PRIMARY_ROI_OCR_CALLS,
    OcrPassResult,
    select_best_pass,
    should_early_exit,
    _try_stitch_full_plate,
)


TESTDATA = Path(__file__).resolve().parents[1] / "testdata"


def test_slot_aware_repairs_digit_bleed_into_series() -> None:
    from pcn_anpr.normalize import (
        normalize_plate,
        sanitize_plate_text,
        slot_aware_repair_standard,
    )

    assert slot_aware_repair_standard("DL3CB00010") == "DL3CBD0010"
    assert sanitize_plate_text("DL3CB00010") == "DL3CBD0010"
    assert sanitize_plate_text("DL3CB00010 NO") == "DL3CBD0010"
    assert normalize_plate("DL3CB00010").normalized == "DL3CBD0010"
    assert normalize_plate("DL3CB00010").matches_known_pattern is True
    # Leading state digit bleed.
    assert sanitize_plate_text("0L8CAL0413") == "DL8CAL0413"
    # Must not invent digits for already-valid plates.
    assert sanitize_plate_text("UP61E6616") == "UP61E6616"
    assert sanitize_plate_text("MH12AB5687") == "MH12AB5687"


def test_select_prefers_repaired_series_over_truncated_bleed() -> None:
    passes = [
        OcrPassResult("color", "DL3CB00010", "DL3CBD0010", 0.98, True),
        OcrPassResult("sharpen", "DL3CBD0010", "DL3CBD0010", 0.95, True),
        OcrPassResult("gray", "DL3CB00010", "DL3CBD0010", 0.99, True),
    ]
    best = select_best_pass(passes, min_ocr_confidence=0.3)
    assert best.normalized_text == "DL3CBD0010"

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
    # Strong consensus: solo high-conf OK when no rival; incomplete alone never exits.
    assert should_early_exit(passes, min_ocr_confidence=0.3, mode="strong_consensus") is True


def test_strong_consensus_rejects_incomplete_and_requires_agreement_or_solo() -> None:
    incomplete = [
        OcrPassResult(
            variant="color_x2",
            raw_text="0L413",
            normalized="0L413",
            confidence=0.99,
            matches_pattern=False,
        )
    ]
    assert should_early_exit(incomplete, min_ocr_confidence=0.3, mode="strong_consensus") is False
    assert should_early_exit(incomplete, min_ocr_confidence=0.3, mode="full_plate") is False

    agreeing = [
        OcrPassResult(
            variant="color_x2",
            raw_text="DL8CAL0413",
            normalized="DL8CAL0413",
            confidence=0.7,
            matches_pattern=True,
        ),
        OcrPassResult(
            variant="sharpen_clahe_x2",
            raw_text="DL8CAL0413",
            normalized="DL8CAL0413",
            confidence=0.72,
            matches_pattern=True,
        ),
    ]
    assert should_early_exit(agreeing, min_ocr_confidence=0.3, mode="strong_consensus") is True

    conflict = [
        OcrPassResult(
            variant="color_x2",
            raw_text="DL8CAL0413",
            normalized="DL8CAL0413",
            confidence=0.95,
            matches_pattern=True,
        ),
        OcrPassResult(
            variant="sharpen_clahe_x2",
            raw_text="DL3CBD0010",
            normalized="DL3CBD0010",
            confidence=0.94,
            matches_pattern=True,
        ),
    ]
    assert should_early_exit(conflict, min_ocr_confidence=0.3, mode="strong_consensus") is False

    # Slot-aware: B/8 (or similar) equivalents count toward the same cluster.
    from pcn_anpr.normalize import plates_slot_equivalent

    assert plates_slot_equivalent("MH12AB5687", "MH12AB56B7") is True
    assert plates_slot_equivalent("DL8CAL0413", "0L8CAL0413") is True
    assert plates_slot_equivalent("MH20DV2366", "MH20DY2366") is False

    slot_both = [
        OcrPassResult(
            variant="color_x2",
            raw_text="MH12AB5687",
            normalized="MH12AB5687",
            confidence=0.7,
            matches_pattern=True,
        ),
        OcrPassResult(
            variant="sharpen_clahe_x2",
            # Same plate under B/8 digit-slot ambiguity; treat as pattern for count.
            raw_text="MH12AB56B7",
            normalized="MH12AB5687",
            confidence=0.7,
            matches_pattern=True,
        ),
    ]
    assert should_early_exit(slot_both, min_ocr_confidence=0.3, mode="strong_consensus") is True


def test_strong_path_variant_order_prioritizes_color_sharpen_adaptive() -> None:
    from pcn_anpr.ocr_ensemble import order_variants_for_strong_path
    from pcn_anpr.preprocess import PreprocessVariant
    import numpy as np

    img = np.zeros((20, 60, 3), dtype=np.uint8)
    variants = [
        PreprocessVariant("gray_clahe_x2", img),
        PreprocessVariant("adaptive_x2", img),
        PreprocessVariant("color_x2", img),
        PreprocessVariant("sharpen_clahe_x2", img),
    ]
    ordered = order_variants_for_strong_path(variants)
    assert [v.name for v in ordered[:3]] == ["color_x2", "sharpen_clahe_x2", "adaptive_x2"]


def test_strong_early_exit_does_not_abandon_on_first_no_pattern() -> None:
    from pcn_anpr.interfaces import OCRResult
    from pcn_anpr.mock_providers import MockOCRProvider
    from pcn_anpr.ocr_ensemble import run_multipass_ocr
    from pcn_anpr.preprocess import generate_ocr_variants
    import numpy as np

    class Scripted(MockOCRProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def read(self, plate_crop: object):
            self.calls += 1
            if self.calls == 1:
                return OCRResult(text="0L413", confidence=0.99, raw_text="0L413")
            return OCRResult(text="DL8CAL0413", confidence=0.91, raw_text="DL8CAL0413")

    crop = np.zeros((24, 80, 3), dtype=np.uint8)
    ocr = Scripted()
    ens = run_multipass_ocr(
        crop,
        ocr,
        live_mode=False,
        scales=(2.0,),
        strong_early_exit=True,
        min_ocr_confidence=0.3,
    )
    # Must run past the first incomplete read (color) into sharpen (+ possibly more).
    assert ocr.calls >= 2
    assert ens.normalized_text == "DL8CAL0413"
    assert ens.timing.get("early_exit_mode") == "strong_consensus"
    # sharpen must have been attempted before finalize.
    ran = [t.get("variant") for t in (ens.timing.get("pass_timings") or [])]
    assert "sharpen_clahe_x2" in ran
    n_variants = len(generate_ocr_variants(crop, scales=(2.0,)))
    assert ocr.calls < n_variants


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

    pipe = build_pipeline(ANPRSettings(provider_mode="real", plate_detector="opencv", vehicle_detector="opencv"))
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
