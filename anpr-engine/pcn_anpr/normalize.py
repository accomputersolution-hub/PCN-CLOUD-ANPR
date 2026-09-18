from __future__ import annotations

"""Indian license-plate normalization and validation.

Shared by the ANPR engine CLI and (via re-export) the backend API.
Confusable O/0 substitutions are OFF by default — never applied blindly.

Supports:
- Standard format: ``MH12AB1234`` / ``MH20DV2366`` / ``TN51Y6552`` / ``KL03AF786``
  (series letters + **1–4** digit unique number — not always four digits)
- Bharat Series (BH): ``22BH6517TA`` (YY BH #### XX)

Non-plate OCR (HSRP ``IND`` legend, stock watermarks like ``alamy``, brand
words) must never be treated as a valid registration.
"""

import re
from dataclasses import dataclass

# Unique number is 1–4 digits (older / motorcycle plates often use 1–3).
INDIAN_STANDARD = re.compile(r"^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{1,4}$")
# Bharat Series: YY BH #### XX  →  22BH6517TA / 22BH6517A
INDIAN_BHARAT = re.compile(r"^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$")

# Find embedded plates inside noisy OCR (e.g. IND22BH6517TA, xxMH12AB1234yy)
_BH_FIND = re.compile(r"[0-9]{2}BH[0-9]{4}[A-Z]{1,2}")
_STD_FIND = re.compile(r"[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{1,4}")
_STD_PARTS = re.compile(r"^[A-Z]{2}([0-9]{1,2})([A-Z]{1,3})([0-9]{1,4})$")

# HSRP left-strip / legend tokens — never a registration number by themselves.
_PLATE_MARKERS = frozenset(
    {
        "IND",
        "IN",
        "ND",
        "BH",
        "INDIA",
        "BHARAT",
    }
)

# Stock-photo / brand OCR that commonly wins high confidence on wrong crops.
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


def is_plate_marker_noise(value: str) -> bool:
    """True for country/legend tokens like IND that must never count as a plate."""
    return strip_plate(value) in _PLATE_MARKERS


def is_non_plate_text(value: str) -> bool:
    """True when OCR text cannot be a valid Indian registration candidate.

    Pattern-matching plates are never treated as noise. Pure English / watermark
    tokens (``alamy``, ``IND``, brand words) are rejected even at high OCR confidence.
    """
    compact = strip_plate(value)
    if not compact:
        return True
    if matches_indian_plate(compact):
        return False
    if is_plate_marker_noise(compact):
        return True
    if compact in _WATERMARK_WORDS:
        return True
    # Taillight / glyph noise often OCR'd as 1–2 arbitrary chars (``IU``, ``8``).
    if len(compact) <= 2:
        return True
    # Alpha-only strings of 3+ letters are almost never RTO plates.
    if compact.isalpha() and len(compact) >= 3:
        return True
    # Digit-only fragments shorter than a unique number are incomplete.
    if compact.isdigit() and len(compact) < 1:
        return True
    return False


def matches_indian_plate(value: str) -> bool:
    """True for Bharat series or standard plates with a known RTO state code.

    Shape-only regex hits like ``AD5XP7941`` (OCR misread of ``KA05KP7941``)
    must not count as valid — ``AD`` is not an Indian state/UT code.
    """
    compact = strip_plate(value)
    if not compact:
        return False
    if INDIAN_BHARAT.match(compact):
        return True
    if not INDIAN_STANDARD.match(compact):
        return False
    return compact[:2] in _INDIAN_STATE_CODES


def stitch_plate_fragments(*parts: str) -> str | None:
    """Try ordered concatenations of OCR fragments into one Indian plate.

    Used when a detector splits a plate into pieces (e.g. ``TN 51`` + ``Y 6552``)
    or an HSRP legend is cropped apart from the registration field.
    Two-line motorcycle plates often yield ``MH02G`` + ``D7249``.
    Returns the first compact string that matches a known Indian pattern.
    """
    cleaned: list[str] = []
    for part in parts:
        compact = sanitize_plate_text(part)
        if not compact:
            continue
        if matches_indian_plate(compact):
            return compact
        # Short 1–2 char tokens are non-plate as *final* OCR (IU / lamps), but
        # digit/alpha fragments like ``22`` / ``MH`` must still stitch.
        if is_non_plate_text(compact):
            if not (len(compact) <= 2 and (compact.isdigit() or compact.isalpha())):
                continue
            if is_plate_marker_noise(compact) or compact in _WATERMARK_WORDS:
                continue
        if compact not in cleaned:
            cleaned.append(compact)
    if len(cleaned) < 2:
        return None

    from itertools import permutations

    trials: list[tuple[str, ...]] = [tuple(cleaned)]
    # Always try every ordered pair — two-line bike plates are usually exactly
    # two fragments (``MH02G`` + ``D7249``), even when noise adds a third token.
    for i, a in enumerate(cleaned):
        for j, b in enumerate(cleaned):
            if i == j:
                continue
            pair = (a, b)
            if pair not in trials:
                trials.append(pair)
    # Four short tokens cover swapped two-line OCR: "AB 5687 MH 12".
    max_tok = max((len(c) for c in cleaned), default=0)
    if len(cleaned) <= 3 or (len(cleaned) == 4 and max_tok <= 5):
        for perm in permutations(cleaned):
            if perm not in trials:
                trials.append(perm)
    elif len(cleaned) <= 6:
        # Limited triples for split one-line plates with an extra noise token.
        for i, a in enumerate(cleaned):
            for j, b in enumerate(cleaned):
                if i == j:
                    continue
                for k, c in enumerate(cleaned):
                    if k in (i, j):
                        continue
                    triple = (a, b, c)
                    if triple not in trials:
                        trials.append(triple)
                    if len(trials) > 120:
                        break
                if len(trials) > 120:
                    break
            if len(trials) > 120:
                break

    scored: list[tuple[float, str]] = []
    for trial in trials:
        joined = "".join(trial)
        candidates = [joined]
        stripped = joined
        while stripped.startswith("IND") and len(stripped) > 3:
            stripped = stripped[3:]
        if stripped != joined:
            candidates.append(stripped)
        for cand in candidates:
            if not matches_indian_plate(cand):
                continue
            # Prefer complete modern plates and two-digit districts over short false joins.
            score = float(len(cand))
            if cand[:2] in _INDIAN_STATE_CODES:
                score += 40.0
            m = _STD_PARTS.match(cand)
            if m:
                if len(m.group(1)) == 2:
                    score += 20.0
                if len(m.group(2)) >= 2:
                    score += 10.0
                # Valid 1–4 digit suffixes; do not force a 4-digit bonus that
                # invents digits (KL03AF786 must beat a padded KL03AF8878 join).
                suffix_len = len(m.group(3))
                if 1 <= suffix_len <= 4:
                    score += 5.0 * suffix_len
            # Prefer joins that consume more source material (MH02G+D7249 > MH02G+7249).
            score += sum(len(p) for p in trial) * 0.25
            scored.append((score, cand))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


# Common Indian RTO state / UT codes (first two letters of standard plates).
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


def _score_embedded_plate(match: str) -> float:
    """Rank embedded plate candidates extracted from noisy OCR."""
    score = float(len(match))
    # Invalid state codes (``AD…``, ``DP…``) are heavily penalized so they lose
    # to a real RTO plate embedded in the same OCR string.
    if INDIAN_BHARAT.match(match):
        score += 50.0
    elif match[:2] in _INDIAN_STATE_CODES:
        score += 40.0
    elif INDIAN_STANDARD.match(match):
        score -= 80.0
    # Mild length preference — do not force a 4-digit unique number.
    if len(match) >= 10:
        score += 12.0
    elif len(match) == 9:
        score += 10.0
    elif len(match) == 8:
        score += 4.0
    elif len(match) <= 7:
        score -= 10.0
    # Prefer two-digit district codes (MH12… / UP78…) over single-digit (DP7…).
    m = _STD_PARTS.match(match)
    if m:
        district, series, tail = m.group(1), m.group(2), m.group(3)
        if len(district) == 2:
            score += 20.0
        else:
            score -= 10.0
        if len(series) >= 2:
            score += 10.0
        # Slight preference for longer suffixes without inventing digits.
        score += min(len(tail), 4) * 2.0
    return score


def sanitize_plate_text(value: str) -> str:
    """Strip HSRP ``IND`` legend and extract the best embedded standard/BH plate."""
    compact = strip_plate(value)
    if not compact:
        return ""
    # Repeated IND prefixes (OCR sometimes doubles the legend)
    while compact.startswith("IND") and len(compact) > 3:
        compact = compact[3:]
    if is_plate_marker_noise(compact):
        return compact
    # Two-line motorcycle OCR often glues the IND legend between rows:
    # ``MH02G IND D7249`` → ``MH02GINDD7249``. Drop embedded IND when that
    # yields a valid registration.
    if "IND" in compact:
        without_ind = compact.replace("IND", "")
        if without_ind and matches_indian_plate(without_ind):
            return without_ind
    # Collect all BH + standard hits; prefer longest / state-coded / complete forms.
    # First-match alone falsely keeps ``DP7F9543`` from grille OCR noise.
    candidates: list[str] = []
    for rx in (_BH_FIND, _STD_FIND):
        for m in rx.finditer(compact):
            candidates.append(m.group(0))
    if "IND" in compact:
        without_ind = compact.replace("IND", "")
        if without_ind:
            candidates.append(without_ind)
            for rx in (_BH_FIND, _STD_FIND):
                for m in rx.finditer(without_ind):
                    candidates.append(m.group(0))
    if candidates:
        # Unique preserve order, then best score.
        uniq: list[str] = []
        for c in candidates:
            if c not in uniq:
                uniq.append(c)
        return max(uniq, key=_score_embedded_plate)
    return compact


def _apply_confusables(compact: str) -> str:
    if matches_indian_plate(compact):
        return compact
    chars = list(compact)
    if len(chars) < 8:
        return compact
    candidate = chars[:]
    # Standard RTO layout digit slots; BH series uses leading year digits instead.
    if INDIAN_BHARAT.match("".join(candidate)) or (len(candidate) >= 4 and candidate[2:4] == ["B", "H"]):
        # YY BH #### XX — digits at 0,1 and 4..7
        digit_idxs = {0, 1, 4, 5, 6, 7}
    else:
        digit_idxs = {2, 3} | set(range(max(0, len(candidate) - 4), len(candidate)))
    for i, ch in enumerate(candidate):
        in_digit_slot = i in digit_idxs
        if in_digit_slot and ch in DIGIT_CONFUSABLES:
            candidate[i] = DIGIT_CONFUSABLES[ch]
        elif not in_digit_slot and ch in LETTER_CONFUSABLES:
            candidate[i] = LETTER_CONFUSABLES[ch]
    joined = "".join(candidate)
    if matches_indian_plate(joined):
        return joined
    return compact


def normalize_plate(raw: str, *, confusable_substitution: bool = False) -> NormalizationResult:
    compact = sanitize_plate_text(raw)
    substitutions = False
    if confusable_substitution and not is_non_plate_text(compact):
        after = _apply_confusables(compact)
        substitutions = after != compact
        compact = after
    return NormalizationResult(
        raw=raw or "",
        normalized=compact,
        substitutions_applied=substitutions,
        matches_known_pattern=matches_indian_plate(compact),
    )
