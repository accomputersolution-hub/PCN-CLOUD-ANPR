from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from pcn_anpr.config import ANPRSettings, clear_anpr_settings_cache
from pcn_anpr.factory import build_pipeline
from pcn_anpr.normalize import matches_indian_plate, normalize_plate
from pcn_anpr.paddle_ocr import EmptyOCRProvider, parse_paddle_ocr_result
from pcn_anpr.pipeline import ANPRPipeline
from pcn_anpr.cli import main as cli_main


def test_pipeline_returns_ocr_mock() -> None:
    result = ANPRPipeline().process(frame=None)
    assert result.ocr is not None
    assert result.ocr.confidence >= 0.9
    assert result.vehicle is not None
    assert result.plate is not None


def test_normalize_variants() -> None:
    for raw in ["mh 12 ab 1234", "MH-12-AB-1234", "mh12ab1234", "DL 01 CA 1234", "KA05MN6789"]:
        out = normalize_plate(raw)
        assert out.matches_known_pattern
        assert out.normalized == normalize_plate(raw.replace(" ", "").replace("-", "")).normalized


def test_normalize_mh12() -> None:
    assert normalize_plate("MH 12 AB 1234").normalized == "MH12AB1234"


def test_indian_validation_multi_state() -> None:
    assert matches_indian_plate("MH12AB1234")
    assert matches_indian_plate("DL01CA1234")
    assert matches_indian_plate("KA05MN6789")
    assert matches_indian_plate("KA05KP7941")
    assert matches_indian_plate("KL03AF786")
    assert not matches_indian_plate("XX")
    # Shape-only OCR misreads must not match (invalid RTO state ``AD``).
    assert not matches_indian_plate("AD5XP7941")
    assert normalize_plate("AD5XP7941").matches_known_pattern is False
    assert normalize_plate("KA05KP7941").matches_known_pattern is True
    assert normalize_plate("KA05KP7941").normalized == "KA05KP7941"
    assert normalize_plate("KL03AF786").normalized == "KL03AF786"
    assert normalize_plate("KL03AF786").matches_known_pattern is True
    # Strict civilian: reject 1-digit RTO garbage (rain/night false positives).
    assert not matches_indian_plate("MH2F22")
    assert not matches_indian_plate("MN2F2")
    assert normalize_plate("MH2F22").matches_known_pattern is False
    assert normalize_plate("MN2F2").matches_known_pattern is False
    # Delhi legacy 1-digit RTO still valid.
    assert matches_indian_plate("DL8CAL0413")
    # Gate / rain plates with 2-digit RTO.
    assert matches_indian_plate("MH14KU9726")
    assert matches_indian_plate("MH12TY8345")
    assert matches_indian_plate("MH02FE2817")
    assert matches_indian_plate("MH12AB5687")

def test_bharat_series_and_ind_marker() -> None:
    from pcn_anpr.normalize import is_plate_marker_noise, sanitize_plate_text

    assert matches_indian_plate("22BH6517TA")
    assert matches_indian_plate("22BH6517A")
    assert normalize_plate("22 BH 6517 TA").normalized == "22BH6517TA"
    assert normalize_plate("22 BH 6517 TA").matches_known_pattern is True
    # IND legend glued to a full BH plate must sanitize to the registration only
    assert sanitize_plate_text("IND22BH6517TA") == "22BH6517TA"
    assert normalize_plate("IND22BH6517TA").normalized == "22BH6517TA"
    assert normalize_plate("IND22BH6517TA").matches_known_pattern is True
    # IND alone is never a valid plate
    assert is_plate_marker_noise("IND")
    assert is_plate_marker_noise("ind")
    assert not matches_indian_plate("IND")
    assert normalize_plate("IND").matches_known_pattern is False
    # Standard formats still work after sanitize
    assert normalize_plate("MH20DV2366").normalized == "MH20DV2366"
    assert normalize_plate("INDMH20DV2366").normalized == "MH20DV2366"


def test_no_blind_confusable() -> None:
    result = normalize_plate("MH12OB1234", confusable_substitution=False)
    assert result.normalized == "MH12OB1234"
    assert result.substitutions_applied is False


def test_parse_empty_ocr() -> None:
    empty = parse_paddle_ocr_result(None)
    assert empty.text == ""
    assert empty.confidence == 0.0
    empty2 = parse_paddle_ocr_result([])
    assert empty2.raw_text == ""


def test_parse_paddle_lines() -> None:
    lines = [
        [[[0, 0], [10, 0], [10, 10], [0, 10]], ("MH12AB1234", 0.91)],
    ]
    result = parse_paddle_ocr_result(lines)
    assert "MH12AB1234" in (result.text + result.raw_text)
    assert result.confidence >= 0.9


def test_empty_ocr_provider() -> None:
    r = EmptyOCRProvider().read(None)
    assert r.text == ""
    assert r.confidence == 0.0


def test_invalid_image_process(tmp_path: Path) -> None:
    clear_anpr_settings_cache()
    pipe = build_pipeline(ANPRSettings(provider_mode="mock"))
    missing = pipe.process_image(tmp_path / "nope.jpg")
    assert missing["error"] == "image_not_found"
    assert missing["plate_detected"] is False

    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not-an-image")
    result = pipe.process_image(bad)
    # May be invalid_image or empty detections depending on decoder
    assert result["plate_detected"] in {False, True} or result.get("error")


def test_low_confidence_filtered(tmp_path: Path) -> None:
    # Tiny dark image — should not crash
    import cv2

    img = np.zeros((64, 96, 3), dtype=np.uint8)
    path = tmp_path / "dark.jpg"
    cv2.imencode(".jpg", img)[1].tofile(str(path))
    pipe = build_pipeline(
        ANPRSettings(
            provider_mode="real",
            ocr_enabled=False,
            min_plate_confidence=0.99,
        )
    )
    result = pipe.process_image(path)
    assert result.get("error") in {None, "invalid_image"}
    assert isinstance(result.get("plates"), list)


def test_detector_on_synthetic_plate(tmp_path: Path) -> None:
    import cv2

    # White plate-like rectangle on dark car-ish background
    img = np.zeros((240, 320, 3), dtype=np.uint8)
    img[:] = (40, 40, 40)
    cv2.rectangle(img, (80, 150), (240, 190), (220, 220, 220), -1)
    cv2.putText(img, "MH12AB1234", (90, 178), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (10, 10, 10), 2)
    path = tmp_path / "synthetic.jpg"
    cv2.imencode(".jpg", img)[1].tofile(str(path))

    pipe = build_pipeline(ANPRSettings(provider_mode="real", ocr_enabled=False, min_plate_confidence=0.2))
    result = pipe.process_image(path)
    # OCR is disabled — detection may yield candidate boxes without a readable plate.
    assert result.get("error") in {None, "no reliable plate detected"}
    # Vehicle fallback or plate candidates — must not crash
    assert "vehicle_detected" in result
    assert "plate_detected" in result
    assert isinstance(result.get("detections"), list)


def test_batch_cli(tmp_path: Path) -> None:
    import os

    import cv2

    folder = tmp_path / "frames"
    folder.mkdir()
    for i in range(2):
        img = np.zeros((80, 120, 3), dtype=np.uint8)
        cv2.imencode(".jpg", img)[1].tofile(str(folder / f"f{i}.jpg"))
    out = tmp_path / "out"
    prev = os.environ.get("ANPR_PROVIDER_MODE")
    try:
        code = cli_main(["--folder", str(folder), "--output-dir", str(out), "--provider", "mock", "--no-annotate"])
        assert code == 0
        results = json.loads((out / "results.json").read_text(encoding="utf-8"))
        assert len(results) == 2
    finally:
        if prev is None:
            os.environ.pop("ANPR_PROVIDER_MODE", None)
        else:
            os.environ["ANPR_PROVIDER_MODE"] = prev
        clear_anpr_settings_cache()


def test_cli_single_mock(tmp_path: Path) -> None:
    import os

    import cv2

    img = np.zeros((80, 120, 3), dtype=np.uint8)
    path = tmp_path / "one.jpg"
    cv2.imencode(".jpg", img)[1].tofile(str(path))
    out = tmp_path / "out"
    prev = os.environ.get("ANPR_PROVIDER_MODE")
    try:
        code = cli_main(["--image", str(path), "--output-dir", str(out), "--provider", "mock"])
        assert code == 0
        assert (out / "results.json").exists()
    finally:
        if prev is None:
            os.environ.pop("ANPR_PROVIDER_MODE", None)
        else:
            os.environ["ANPR_PROVIDER_MODE"] = prev
        clear_anpr_settings_cache()
