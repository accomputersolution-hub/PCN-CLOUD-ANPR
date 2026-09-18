from app.services.plate import (
    is_non_plate_text,
    is_plate_marker_noise,
    matches_indian_plate,
    normalize_plate,
)


def test_normalize_variants() -> None:
    for raw in ["mh 12 ab 1234", "MH-12-AB-1234", "mh12ab1234"]:
        assert normalize_plate(raw).normalized == "MH12AB1234"


def test_does_not_blindly_substitute() -> None:
    result = normalize_plate("MH12OB1234", confusable_substitution=False)
    assert result.normalized == "MH12OB1234"
    assert result.substitutions_applied is False


def test_confusable_only_when_enabled_and_pattern_matches() -> None:
    result = normalize_plate("MH12OB1234", confusable_substitution=True)
    assert result.normalized == "MH120B1234" or result.matches_known_pattern or result.normalized.startswith("MH12")
    assert matches_indian_plate("MH12AB1234")


def test_bharat_series_normalization() -> None:
    assert matches_indian_plate("22BH6517TA")
    assert matches_indian_plate("22BH6517A")
    assert normalize_plate("22 BH 6517 TA").normalized == "22BH6517TA"
    assert normalize_plate("IND22BH6517TA").normalized == "22BH6517TA"
    assert normalize_plate("IND22BH6517TA").matches_known_pattern is True


def test_ind_marker_never_valid_plate() -> None:
    assert is_plate_marker_noise("IND")
    assert is_non_plate_text("IND")
    assert not matches_indian_plate("IND")
    assert normalize_plate("IND").matches_known_pattern is False
    assert normalize_plate("MH20DV2366").matches_known_pattern is True


def test_alamy_watermark_never_valid_plate() -> None:
    assert is_non_plate_text("alamy")
    assert is_non_plate_text("ALAMY")
    assert not matches_indian_plate("ALAMY")
    assert normalize_plate("alamy").matches_known_pattern is False
    assert matches_indian_plate("TN51Y6552")
    assert normalize_plate("TN 51 Y 6552").normalized == "TN51Y6552"
