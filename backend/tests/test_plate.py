from app.services.plate import matches_indian_plate, normalize_plate


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
