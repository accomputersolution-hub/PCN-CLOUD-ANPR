from __future__ import annotations

"""Indian license-plate normalization and validation.

Shared by the ANPR engine CLI and (via re-export) the backend API.
Confusable O/0 substitutions are OFF by default — never applied blindly.
"""

import re
from dataclasses import dataclass

INDIAN_STANDARD = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}$")
INDIAN_BHARAT = re.compile(r"^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$")

DIGIT_CONFUSABLES = {"O": "0", "I": "1", "Z": "2", "S": "5", "B": "8"}
LETTER_CONFUSABLES = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B"}


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


def _apply_confusables(compact: str) -> str:
    if matches_indian_plate(compact):
        return compact
    chars = list(compact)
    if len(chars) < 8:
        return compact
    candidate = chars[:]
    for i, ch in enumerate(candidate):
        in_digit_slot = i in {2, 3} or i >= len(candidate) - 4
        if in_digit_slot and ch in DIGIT_CONFUSABLES:
            candidate[i] = DIGIT_CONFUSABLES[ch]
        elif not in_digit_slot and ch in LETTER_CONFUSABLES:
            candidate[i] = LETTER_CONFUSABLES[ch]
    joined = "".join(candidate)
    if matches_indian_plate(joined):
        return joined
    return compact


def normalize_plate(raw: str, *, confusable_substitution: bool = False) -> NormalizationResult:
    compact = strip_plate(raw)
    substitutions = False
    if confusable_substitution:
        after = _apply_confusables(compact)
        substitutions = after != compact
        compact = after
    return NormalizationResult(
        raw=raw or "",
        normalized=compact,
        substitutions_applied=substitutions,
        matches_known_pattern=matches_indian_plate(compact),
    )
