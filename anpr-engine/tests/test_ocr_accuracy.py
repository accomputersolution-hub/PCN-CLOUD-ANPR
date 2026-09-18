from __future__ import annotations

from pathlib import Path
from shutil import copyfile

import numpy as np
import pytest

from pcn_anpr.config import ANPRSettings, clear_anpr_settings_cache
from pcn_anpr.normalize import matches_indian_plate, normalize_plate, stitch_plate_fragments
from pcn_anpr.ocr_ensemble import OcrPassResult, select_best_pass
from pcn_anpr.preprocess import crop_with_padding, generate_ocr_variants


FIXTURE_SRC = Path(
    r"C:\Users\mdsal\StudioProjects\car managment\edge-agent\edge-agent\data\edge-frames\test_plate.jpg"
)
FIXTURE_LOCAL = Path(__file__).resolve().parent.parent / "testdata" / "MH01EP9019.jpg"


@pytest.fixture(scope="module")
def mh01_image(tmp_path_factory) -> Path:
    dest = FIXTURE_LOCAL
    dest.parent.mkdir(parents=True, exist_ok=True)
    if FIXTURE_SRC.is_file():
        copyfile(FIXTURE_SRC, dest)
    if not dest.is_file():
        pytest.skip("MH01EP9019 test image not available")
    return dest


def test_crop_padding_expands_right() -> None:
    img = np.zeros((100, 200, 3), dtype=np.uint8)
    crop, box = crop_with_padding(img, 50, 40, 120, 60, pad_ratio=0.2, pad_px_min=4)
    assert crop is not None
    x1, y1, x2, y2 = box
    assert x1 < 50
    assert x2 > 120  # extra right padding
    assert y1 < 40 and y2 > 60


def test_generate_variants_names() -> None:
    img = np.full((40, 120, 3), 200, dtype=np.uint8)
    variants = generate_ocr_variants(img, scales=(2.0,))
    names = {v.name for v in variants}
    assert any("clahe" in n for n in names)
    assert any("sharpen" in n for n in names)
    assert any("adaptive" in n for n in names)


def test_select_prefers_full_pattern_over_truncated() -> None:
    passes = [
        OcrPassResult("a", "MH01EP9", "MH01EP9", 0.99, False),
        OcrPassResult("b", "MH01EP9019", "MH01EP9019", 0.92, True),
        OcrPassResult("c", "MH01EP9019", "MH01EP9019", 0.88, True),
    ]
    best = select_best_pass(passes, min_ocr_confidence=0.3)
    assert best.normalized_text == "MH01EP9019"
    assert best.ocr_confident is True
    assert best.raw_text.replace(" ", "") == "MH01EP9019"


def test_select_not_confident_sets_null_normalized() -> None:
    passes = [
        OcrPassResult("a", "MH01EP9", "MH01EP9", 0.99, False),
    ]
    best = select_best_pass(passes, min_ocr_confidence=0.3)
    assert best.matches_pattern is False
    assert best.ocr_confident is False
    assert best.normalized_text is None
    assert best.raw_text == "MH01EP9"


def test_select_rejects_invalid_state_ocr() -> None:
    """Motorcycle OCR misread ``AD5XP7941`` must lose to real ``KA05KP7941``."""
    from pcn_anpr.ocr_ensemble import OcrPassResult, select_best_pass

    passes = [
        OcrPassResult("bad", "AD5XP7941", "AD5XP7941", 0.97, False),
        OcrPassResult("good", "KA05KP7941", "KA05KP7941", 0.82, True),
    ]
    best = select_best_pass(passes, min_ocr_confidence=0.3)
    assert best.normalized_text == "KA05KP7941"
    assert best.ocr_confident is True


def test_select_invalid_state_alone_not_confident() -> None:
    from pcn_anpr.ocr_ensemble import OcrPassResult, select_best_pass

    passes = [OcrPassResult("a", "AD5XP7941", "AD5XP7941", 0.99, False)]
    best = select_best_pass(passes, min_ocr_confidence=0.3)
    assert best.ocr_confident is False
    assert best.normalized_text is None
    assert best.matches_pattern is False


def test_ka05_plate_crop_ocr_recovers_valid_state(tmp_path: Path) -> None:
    path = Path(__file__).resolve().parents[1] / "testdata" / "KA05KP7941_plate_crop.jpg"
    if not path.is_file():
        pytest.skip("KA05 plate crop fixture missing")
    clear_anpr_settings_cache()
    from pcn_anpr.config import ANPRSettings
    from pcn_anpr.factory import build_pipeline
    from pcn_anpr.ocr_ensemble import run_multipass_ocr
    import cv2

    img = cv2.imread(str(path))
    assert img is not None
    pipe = build_pipeline(ANPRSettings(provider_mode="real", plate_detector="opencv"))
    pipe.warm_up()
    ens = run_multipass_ocr(
        img,
        pipe.ocr,
        min_ocr_confidence=0.3,
        adaptive_fast_path=True,
    )
    assert ens.normalized_text == "KA05KP7941"
    assert ens.ocr_confident is True
    assert matches_indian_plate(ens.normalized_text or "")
    assert not matches_indian_plate("AD5XP7941")


def test_does_not_invent_characters() -> None:
    # Pattern match only if OCR actually produced a matching string — no digit invention.
    assert not matches_indian_plate("MH01EP")  # series with no unique number
    assert matches_indian_plate("MH01EP9")  # valid 1-digit unique number
    assert matches_indian_plate("MH01EP9019")
    assert normalize_plate("MH01EP").matches_known_pattern is False
    assert normalize_plate("KL03AF786").normalized == "KL03AF786"
    assert normalize_plate("KL03AF786").matches_known_pattern is True


def test_select_prefers_three_digit_over_invented_four() -> None:
    """KL03AF786 must win over competing KL03AF8878 when both appear."""
    passes = [
        OcrPassResult("a", "KL03AF8878", "KL03AF8878", 0.97, True),
        OcrPassResult("b", "KL 03 AF 786", "KL03AF786", 0.88, True),
        OcrPassResult("c", "KL03AF786", "KL03AF786", 0.91, True),
    ]
    best = select_best_pass(passes, min_ocr_confidence=0.3)
    assert best.normalized_text == "KL03AF786"
    assert best.ocr_confident is True


def test_twoline_kl03af786_normalize() -> None:
    assert matches_indian_plate("KL03AF786")
    assert matches_indian_plate("KL03AF7")
    assert matches_indian_plate("KL03AF78")
    assert matches_indian_plate("KL03AF7860")  # 4-digit still ok
    assert normalize_plate("KL 03 AF\n786").normalized == "KL03AF786"
    assert stitch_plate_fragments("KL 03 AF", "786") == "KL03AF786"
    assert stitch_plate_fragments("786", "KL03AF") == "KL03AF786"


def test_mh01ep9019_image_recovers_full_plate(mh01_image: Path, tmp_path: Path) -> None:
    clear_anpr_settings_cache()
    from pcn_anpr.factory import build_pipeline

    settings = ANPRSettings(
        provider_mode="real",
        ocr_save_debug_crops=True,
        ocr_debug_dir=str(tmp_path / "ocr_debug"),
        output_dir=str(tmp_path / "out"),
        plate_pad_ratio=0.22,
        min_ocr_confidence=0.3,
    )
    pipe = build_pipeline(settings)
    result = pipe.process_image(mh01_image)
    assert result["plate_detected"] is True
    plates = result.get("plates") or []
    assert plates, "expected at least one plate detection"
    best = plates[0]
    raw = (best.get("raw_text") or "").replace(" ", "").upper()
    norm = best.get("normalized_text")
    # Prefer full recovery; if OCR still truncates, fail clearly for this regression test
    assert "MH01EP9019" in raw or norm == "MH01EP9019", (
        f"expected MH01EP9019, got raw={best.get('raw_text')!r} norm={norm!r} "
        f"passes={best.get('ocr_passes')}"
    )
    assert best.get("ocr_confident") is True
    assert norm == "MH01EP9019"
    # Debug crops should exist when enabled
    assert best.get("debug_crops"), "expected debug preprocessing crops"
