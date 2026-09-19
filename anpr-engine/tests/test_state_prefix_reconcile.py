"""Slot-aware state-prefix reconciliation (OCR-only; no detector changes)."""

from __future__ import annotations

from pcn_anpr.normalize import (
    is_near_pattern_invalid_state,
    matches_indian_plate,
    normalize_plate,
    reconcile_state_prefix,
    sanitize_plate_text,
    slot_aware_repair_standard,
)


def test_hh12vf8354_reconciles_to_mh() -> None:
    """High-mount CCTV: OCR HH12VF8354 → MH12VF8354 (H↔M on state slot only)."""
    assert is_near_pattern_invalid_state("HH12VF8354") is True
    assert reconcile_state_prefix("HH12VF8354") == "MH12VF8354"
    assert slot_aware_repair_standard("HH12VF8354") == "MH12VF8354"
    assert sanitize_plate_text("HH12VF8354") == "MH12VF8354"
    out = normalize_plate("HH12VF8354")
    assert out.normalized == "MH12VF8354"
    assert out.matches_known_pattern is True
    assert matches_indian_plate("MH12VF8354")


def test_hh14kp7206_scooter_near_miss_state_only() -> None:
    """Scooter near-miss: fix state prefix only — do not invent digit corrections."""
    assert reconcile_state_prefix("HH14KP7206") == "MH14KP7206"
    assert normalize_plate("HH14KP7206").normalized == "MH14KP7206"
    assert normalize_plate("HH14KP7206").matches_known_pattern is True
    # Must not invent 7286 when OCR evidence is 7206.
    assert normalize_plate("HH14KP7206").normalized != "MH14KP7286"
    # WH (invalid) → MH via W↔M state-slot confusion (seen on scooter crop).
    assert reconcile_state_prefix("WH14KP7206") == "MH14KP7206"
    assert normalize_plate("WH14KP7206").normalized == "MH14KP7206"


def test_reject_garbage_and_weak_structure() -> None:
    """Never accept arbitrary / short / 1-digit-RTO garbage transformations."""
    assert reconcile_state_prefix("HH2F22") is None
    assert reconcile_state_prefix("MH2F22") is None
    assert reconcile_state_prefix("MN2F2") is None
    assert reconcile_state_prefix("XX12AB1234") is None
    assert reconcile_state_prefix("QQ12AB1234") is None
    assert reconcile_state_prefix("HH") is None
    assert reconcile_state_prefix("HH12") is None
    assert normalize_plate("MH2F22").matches_known_pattern is False
    assert normalize_plate("MN2F2").matches_known_pattern is False
    assert normalize_plate("AD5XP7941").matches_known_pattern is False
    assert is_near_pattern_invalid_state("MH2F22") is False
    assert is_near_pattern_invalid_state("MH12AB5687") is False


def test_no_global_h_to_m_inside_plate() -> None:
    """H elsewhere in the plate must not be blindly rewritten to M."""
    # Valid MH plate with H in series / already-correct state — untouched.
    assert normalize_plate("MH12AH1234").normalized == "MH12AH1234"
    assert matches_indian_plate("MH12AH1234")
    # Valid HR plate must stay HR (not forced to MR/MH).
    assert normalize_plate("HR26DK8337").normalized == "HR26DK8337"
    assert normalize_plate("HR26DK8337").matches_known_pattern is True


def test_preserve_known_good_plates() -> None:
    """Regression: existing correct reads must stay stable."""
    for plate in (
        "MH12UF7286",
        "MH12AB5687",
        "MH14LE4050",
        "MH05EK3142",
        "DL8CAL0413",
        "DL3CBD0010",
        "UP61E6416",
        "UP61E6616",
        "MH12VF8354",
        "MH14KP7286",
    ):
        out = normalize_plate(plate)
        assert out.normalized == plate
        assert out.matches_known_pattern is True
        assert reconcile_state_prefix(plate) == plate or reconcile_state_prefix(plate) is None
        # Already-valid: reconcile returns the plate itself when matches.
        assert reconcile_state_prefix(plate) == plate
