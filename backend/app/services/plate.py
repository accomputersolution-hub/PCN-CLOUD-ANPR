from __future__ import annotations

"""Indian plate normalization — shared with ANPR engine when available."""

try:
    from pcn_anpr.normalize import (
        INDIAN_BHARAT,
        INDIAN_STANDARD,
        NormalizationResult,
        is_non_plate_text,
        is_plate_marker_noise,
        matches_indian_plate,
        normalize_plate,
        sanitize_plate_text,
        strip_plate,
    )
except ImportError:  # pragma: no cover — backend running without anpr-engine on PYTHONPATH
    import re
    from dataclasses import dataclass

    INDIAN_STANDARD = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{1,4}$")
    INDIAN_BHARAT = re.compile(r"^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$")
    _BH_FIND = re.compile(r"[0-9]{2}BH[0-9]{4}[A-Z]{1,2}")
    _STD_FIND = re.compile(r"[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{1,4}")
    _PLATE_MARKERS = frozenset({"IND", "IN", "ND", "BH", "INDIA", "BHARAT"})
    _INDIAN_STATE_CODES = frozenset(
        {
            "AN",
            "AP",
            "AR",
            "AS",
            "BR",
            "CG",
            "CH",
            "DD",
            "DL",
            "DN",
            "GA",
            "GJ",
            "HP",
            "HR",
            "JH",
            "JK",
            "KA",
            "KL",
            "LA",
            "LD",
            "MH",
            "ML",
            "MN",
            "MP",
            "MZ",
            "NL",
            "OD",
            "PB",
            "PY",
            "RJ",
            "SK",
            "TN",
            "TR",
            "TS",
            "UK",
            "UP",
            "WB",
        }
    )
    _WATERMARK_WORDS = frozenset(
        {
            "ALAMY",
            "GETTY",
            "SHUTTERSTOCK",
            "ISTOCK",
            "ADOBESTOCK",
            "ADOBE",
            "DREAMSTIME",
            "DEPOSITPHOTOS",
            "SKODA",
            "HYUNDAI",
            "HONDA",
            "TOYOTA",
            "MARUTI",
            "TATA",
            "LAURA",
            "BULLET",
            "SANTRO",
            "XING",
            "VRS",
            "COPYRIGHT",
            "WATERMARK",
        }
    )

    @dataclass(frozen=True)
    class NormalizationResult:
        raw: str
        normalized: str
        substitutions_applied: bool
        matches_known_pattern: bool

    def strip_plate(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9]", "", value or "").upper()

    def is_plate_marker_noise(value: str) -> bool:
        return strip_plate(value) in _PLATE_MARKERS

    def matches_indian_plate(value: str) -> bool:
        compact = strip_plate(value)
        if not compact:
            return False
        if INDIAN_BHARAT.match(compact):
            return True
        if not INDIAN_STANDARD.match(compact):
            return False
        return compact[:2] in _INDIAN_STATE_CODES

    def is_non_plate_text(value: str) -> bool:
        compact = strip_plate(value)
        if not compact:
            return True
        if matches_indian_plate(compact):
            return False
        if is_plate_marker_noise(compact):
            return True
        if compact in _WATERMARK_WORDS:
            return True
        if len(compact) <= 2:
            return True
        if compact.isalpha() and len(compact) >= 3:
            return True
        return False

    def sanitize_plate_text(value: str) -> str:
        compact = strip_plate(value)
        while compact.startswith("IND") and len(compact) > 3:
            compact = compact[3:]
        if is_plate_marker_noise(compact):
            return compact
        for rx in (_BH_FIND, _STD_FIND):
            m = rx.search(compact)
            if m:
                return m.group(0)
        return compact

    def normalize_plate(raw: str, *, confusable_substitution: bool = False) -> NormalizationResult:
        _ = confusable_substitution
        compact = sanitize_plate_text(raw)
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
    "is_non_plate_text",
    "is_plate_marker_noise",
    "matches_indian_plate",
    "normalize_plate",
    "sanitize_plate_text",
    "strip_plate",
]
