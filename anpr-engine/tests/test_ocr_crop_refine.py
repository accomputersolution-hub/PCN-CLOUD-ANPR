from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from pcn_anpr.crop_refine import is_loose_vehicle_front_crop, refine_loose_plate_crop
from pcn_anpr.normalize import normalize_plate, sanitize_plate_text


def test_sanitize_prefers_complete_state_plate_over_short_false_embed() -> None:
    assert sanitize_plate_text("COOGDDD JEUD UP78FZ9543") == "UP78FZ9543"
    assert normalize_plate("noise UP78FZ9543 IND").normalized == "UP78FZ9543"
    assert normalize_plate("noise UP78FZ9543 IND").matches_known_pattern


def test_loose_front_crop_detected() -> None:
    tall = np.zeros((210, 461, 3), dtype=np.uint8)
    assert is_loose_vehicle_front_crop(tall) is True
    tight = np.zeros((40, 220, 3), dtype=np.uint8)
    assert is_loose_vehicle_front_crop(tight) is False
    # Portrait motorcycle rear — must not trigger Scorpio lower-band refine.
    portrait = np.zeros((734, 417, 3), dtype=np.uint8)
    assert is_loose_vehicle_front_crop(portrait) is False
    twoline = np.zeros((90, 180, 3), dtype=np.uint8)
    assert is_loose_vehicle_front_crop(twoline) is False


def test_refine_or_lowerband_recovers_up78_from_loose_crop() -> None:
    lower_path = Path("output/up78_lower.jpg")
    top_path = Path("output/up78_top.jpg")
    mid_path = Path("output/up78_mid.jpg")
    fixture = Path("testdata/UP78FZ9543_scorpio_crop.jpg")
    if fixture.is_file():
        loose = cv2.imread(str(fixture))
    elif lower_path.is_file() and top_path.is_file() and mid_path.is_file():
        w = 461
        loose = np.vstack(
            [
                cv2.resize(cv2.imread(str(top_path)), (w, 70)),
                cv2.resize(cv2.imread(str(mid_path)), (w, 35)),
                cv2.resize(cv2.imread(str(lower_path)), (w, 105)),
            ]
        )
    else:
        pytest.skip("UP78 loose crop materials missing")

    assert is_loose_vehicle_front_crop(loose)
    # Lower band must OCR to the real plate (pipeline retry path).
    from pcn_anpr.config import ANPRSettings, clear_anpr_settings_cache
    from pcn_anpr.factory import build_pipeline
    from pcn_anpr.ocr_ensemble import run_multipass_ocr

    h, w = loose.shape[:2]
    band = loose[int(h * 0.52) :, int(w * 0.05) : int(w * 0.95)]
    clear_anpr_settings_cache()
    pipe = build_pipeline(ANPRSettings(provider_mode="real", ocr_enabled=True))
    pipe.warm_up()
    ens = run_multipass_ocr(
        band,
        pipe.ocr,
        min_ocr_confidence=0.55,
        scales=(2.0, 3.0),
        live_mode=False,
        early_exit_on_confident=True,
        adaptive_fast_path=True,
    )
    assert ens.normalized_text == "UP78FZ9543"
    assert ens.matches_pattern
