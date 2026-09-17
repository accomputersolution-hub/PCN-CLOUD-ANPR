from __future__ import annotations

"""Indian plate normalization — shared with ANPR engine when available."""

try:
    from pcn_anpr.normalize import (
        INDIAN_BHARAT,
        INDIAN_STANDARD,
        NormalizationResult,
        matches_indian_plate,
        normalize_plate,
        strip_plate,
    )
except ImportError:  # pragma: no cover — backend running without anpr-engine on PYTHONPATH
    import re
    from dataclasses import dataclass

    INDIAN_STANDARD = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}$")
    INDIAN_BHARAT = re.compile(r"^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$")

    @dataclass(frozen=True)
    class NormalizationResult:
        raw: str
        normalized: str
        substitutions_applied: bool
        matches_known_pattern: bool

    def strip_plate(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9]", "", value or "").upper()

    def matches_indian_plate(value: str) -> bool:
        return bool(INDIAN_STANDARD.match(value) or INDIAN_BHARAT.match(value))

    def normalize_plate(raw: str, *, confusable_substitution: bool = False) -> NormalizationResult:
        compact = strip_plate(raw)
        return NormalizationResult(
            raw=raw or "",
            normalized=compact,
            substitutions_applied=False,
            matches_known_pattern=matches_indian_plate(compact),
        )

__all__ = [
    "INDIAN_BHARAT",
    "INDIAN_STANDARD",
    "NormalizationResult",
    "matches_indian_plate",
    "normalize_plate",
    "strip_plate",
]
