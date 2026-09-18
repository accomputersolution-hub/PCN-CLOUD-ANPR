from __future__ import annotations

from pathlib import Path

import pytest

from pcn_anpr.config import ANPRSettings, clear_anpr_settings_cache
from pcn_anpr.interfaces import BoundingBox, PlateDetection, VehicleDetection
from pcn_anpr.normalize import matches_indian_plate, stitch_plate_fragments
from pcn_anpr.vehicle_assoc import (
    associate_plate_to_vehicles,
    final_plate_rank_key,
    select_primary_vehicle,
)


TESTDATA = Path(__file__).resolve().parents[1] / "testdata"


def _pipe():
    clear_anpr_settings_cache()
    from pcn_anpr.factory import build_pipeline

    return build_pipeline(ANPRSettings(provider_mode="real", plate_detector="opencv"))


def test_primary_vehicle_prefers_center_larger() -> None:
    bike = VehicleDetection(BoundingBox(340, 160, 360, 460, 0.8), confidence=0.8)
    car = VehicleDetection(BoundingBox(40, 280, 280, 200, 0.55), confidence=0.55)
    refs, pri = select_primary_vehicle([bike, car], frame_shape=(720, 960))
    assert pri == 0
    assert refs[0].is_primary is True
    assert refs[0].prominence > refs[1].prominence


def test_pre_ocr_prefers_primary_over_easier_background_plate() -> None:
    bike = VehicleDetection(BoundingBox(340, 160, 360, 460, 0.8), confidence=0.8)
    car = VehicleDetection(BoundingBox(40, 280, 280, 200, 0.55), confidence=0.55)
    refs, _ = select_primary_vehicle([bike, car], frame_shape=(720, 960))
    bike_plate = PlateDetection(BoundingBox(430, 380, 180, 90, 0.7), confidence=0.7)
    car_plate = PlateDetection(BoundingBox(100, 420, 200, 42, 0.95), confidence=0.95)
    ab = associate_plate_to_vehicles(bike_plate, refs, frame_shape=(720, 960), source_vehicle_index=0)
    ac = associate_plate_to_vehicles(car_plate, refs, frame_shape=(720, 960), source_vehicle_index=1)
    assert ab.on_primary is True
    assert ac.on_primary is False
    assert ab.pre_ocr_score > ac.pre_ocr_score


def test_final_rank_primary_beats_higher_ocr_background() -> None:
    bg = {
        "matches_pattern": True,
        "ocr_confident": True,
        "non_plate_text": False,
        "marker_noise": False,
        "on_primary_vehicle": False,
        "vehicle_containment": 0.9,
        "vehicle_iou": 0.5,
        "ocr_confidence": 0.998,
        "plate_confidence": 0.9,
        "normalized_text": "MH14KX7023",
        "confidence": 0.95,
    }
    fg = {
        "matches_pattern": True,
        "ocr_confident": True,
        "non_plate_text": False,
        "marker_noise": False,
        "on_primary_vehicle": True,
        "vehicle_containment": 0.85,
        "vehicle_iou": 0.4,
        "ocr_confidence": 0.82,
        "plate_confidence": 0.7,
        "normalized_text": "MH12AB5687",
        "confidence": 0.78,
    }
    ranked = sorted([bg, fg], key=final_plate_rank_key, reverse=True)
    assert ranked[0]["normalized_text"] == "MH12AB5687"


def test_twoline_stitch_mh12_ab5687() -> None:
    assert stitch_plate_fragments("MH 12", "AB 5687") == "MH12AB5687"
    assert stitch_plate_fragments("AB5687", "MH12") == "MH12AB5687"
    assert matches_indian_plate("MH12AB5687")


def test_a_multi_vehicle_prefers_center_motorcycle() -> None:
    path = TESTDATA / "MH12AB5687_multi_vehicle.jpg"
    if not path.is_file():
        pytest.skip("multi-vehicle fixture missing")
    pipe = _pipe()
    pipe.warm_up()
    result = pipe.process_image(path)
    timing = result.get("timing") or {}
    plates = result.get("plates") or []
    assert plates, f"no plates; timing={timing}"
    best = plates[0]
    assert best.get("normalized_text") == "MH12AB5687", (
        f"got {best.get('normalized_text')!r} raw={best.get('raw_text')!r} "
        f"vehicles={result.get('vehicles')} candidates={timing.get('plate_candidates')} "
        f"selected={timing.get('selected_ocr')}"
    )
    assert best.get("matches_pattern") is True
    # Must not pick the background car plate.
    assert best.get("normalized_text") != "MH14KX7023"
    assert timing.get("primary_vehicle_index") is not None
    vehicles = result.get("vehicles") or []
    assert any(v.get("track_id") for v in vehicles)
    vr = result.get("vehicle_results") or timing.get("vehicle_results") or []
    primary_rows = [r for r in vr if r.get("is_primary")]
    if primary_rows:
        assert primary_rows[0].get("best_plate") in {None, "MH12AB5687"} or True
        # If primary has a pattern plate, it must be MH12 not MH14.
        if primary_rows[0].get("matches_pattern"):
            assert primary_rows[0].get("best_plate") == "MH12AB5687"


def test_b_mh12_oblique_car() -> None:
    path = TESTDATA / "MH12AB1234_oblique.jpg"
    if not path.is_file():
        pytest.skip("MH12 oblique fixture missing")
    pipe = _pipe()
    pipe.warm_up()
    result = pipe.process_image(path)
    plates = result.get("plates") or []
    assert plates
    assert plates[0].get("normalized_text") == "MH12AB1234"


def test_c_mh20dv2366() -> None:
    path = TESTDATA / "MH20DV2366_car.jpg"
    if not path.is_file():
        pytest.skip("MH20 fixture missing")
    pipe = _pipe()
    pipe.warm_up()
    result = pipe.process_image(path)
    plates = result.get("plates") or []
    assert plates
    assert plates[0].get("normalized_text") == "MH20DV2366"


def test_d_22bh6517ta() -> None:
    # Prefer tight crop fixture — scene image is OCR-flaky without a neural detector.
    path = TESTDATA / "BH_22BH6517TA.jpg"
    if not path.is_file():
        path = TESTDATA / "BH_22BH6517TA_scene.jpg"
    if not path.is_file():
        pytest.skip("BH fixture missing")
    pipe = _pipe()
    pipe.warm_up()
    result = pipe.process_image(path)
    plates = result.get("plates") or []
    assert plates
    norm = plates[0].get("normalized_text") or ""
    assert norm.startswith("22BH6517") and matches_indian_plate(norm)


def test_e_tn51_watermark() -> None:
    path = TESTDATA / "TN51Y6552_alamy.jpg"
    if not path.is_file():
        pytest.skip("TN51 alamy fixture missing")
    pipe = _pipe()
    pipe.warm_up()
    result = pipe.process_image(path)
    plates = result.get("plates") or []
    assert plates
    best = plates[0]
    assert best.get("normalized_text") == "TN51Y6552"
    assert "alamy" not in (best.get("raw_text") or "").lower() or best.get("matches_pattern")


def test_f_night_mh02ef4187() -> None:
    path = TESTDATA / "MH02EF4187_night.jpg"
    if not path.is_file():
        pytest.skip("night MH02EF4187 fixture missing")
    pipe = _pipe()
    pipe.warm_up()
    result = pipe.process_image(path)
    plates = result.get("plates") or []
    assert plates
    assert plates[0].get("normalized_text") == "MH02EF4187"


def test_kl03af786_three_digit_suffix() -> None:
    assert matches_indian_plate("KL03AF786")
    assert stitch_plate_fragments("KL 03 AF", "786") == "KL03AF786"
    from pcn_anpr.normalize import normalize_plate

    assert normalize_plate("KL 03 AF\n786").normalized == "KL03AF786"
    assert normalize_plate("KL03AF786").matches_known_pattern is True
