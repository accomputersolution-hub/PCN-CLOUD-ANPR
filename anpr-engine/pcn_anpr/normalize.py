from __future__ import annotations

"""Indian license-plate normalization and validation.

Shared by the ANPR engine CLI and (via re-export) the backend API.
Confusable O/0 substitutions are OFF by default — never applied blindly.

Supports:
- Standard civilian format: ``MH12AB1234`` / ``MH20DV2366`` / ``TN51Y6552`` /
  ``KL03AF786`` — **exactly 2** state letters + **exactly 2** RTO digits +
  series letters + **1–4** digit unique number (not always four digits).
  Short garbage like ``MH2F22`` / ``MN2F2`` (1-digit RTO) is rejected.
- Delhi legacy 1-digit RTO: ``DL8CAL0413`` (DL only).
- Bharat Series (BH): ``22BH6517TA`` (YY BH #### XX)

Non-plate OCR (HSRP ``IND`` legend, stock watermarks like ``alamy``, brand
words) must never be treated as a valid registration.
"""

import re
from dataclasses import dataclass

# Civilian standard: SS + DD + series + unique (1–4). Exactly 2 RTO digits.
INDIAN_STANDARD = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z]{1,3}[0-9]{1,4}$")
# Delhi often uses a single RTO digit (``DL8CAL0413``) — not for other states.
INDIAN_STANDARD_DL_1RTO = re.compile(r"^DL[0-9][A-Z]{1,3}[0-9]{1,4}$")
# Bharat Series: YY BH #### XX  →  22BH6517TA / 22BH6517A
INDIAN_BHARAT = re.compile(r"^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$")

# Find embedded plates inside noisy OCR (e.g. IND22BH6517TA, xxMH12AB1234yy)
_BH_FIND = re.compile(r"[0-9]{2}BH[0-9]{4}[A-Z]{1,2}")
_STD_FIND = re.compile(r"[A-Z]{2}[0-9]{2}[A-Z]{1,3}[0-9]{1,4}")
_STD_FIND_DL = re.compile(r"DL[0-9][A-Z]{1,3}[0-9]{1,4}")
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

DIGIT_CONFUSABLES = {"O": "0", "I": "1", "Z": "2", "S": "5", "B": "8", "D": "0"}
LETTER_CONFUSABLES = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B"}
# Slot-aware ambiguity groups for consensus (NOT blind global replace).
_SLOT_AMBIGUITY_GROUPS: tuple[frozenset[str], ...] = (
    frozenset({"D", "0"}),
    frozenset({"B", "8"}),
    frozenset({"O", "0"}),
    frozenset({"I", "1"}),
    frozenset({"S", "5"}),
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
    """True for Bharat series or standard civilian plates with a known state code.

    Civilian finals require **exactly 2** RTO digits (``MH12AB1234``). Short
    1-digit-RTO garbage like ``MH2F22`` / ``MN2F2`` is rejected. Delhi legacy
    1-digit RTO (``DL8CAL0413``) is the only intentional exception.

    Shape-only regex hits like ``AD5XP7941`` (OCR misread of ``KA05KP7941``)
    must not count as valid — ``AD`` is not an Indian state/UT code.

    Also rejects duplicated top-line OCR such as ``MH12MH12`` (series equals
    the state code) which is never a real registration.
    """
    compact = strip_plate(value)
    if not compact:
        return False
    if INDIAN_BHARAT.match(compact):
        return True
    is_civilian = bool(INDIAN_STANDARD.match(compact))
    is_dl_legacy = bool(INDIAN_STANDARD_DL_1RTO.match(compact))
    if not is_civilian and not is_dl_legacy:
        return False
    if compact[:2] not in _INDIAN_STATE_CODES:
        return False
    # ``MH12MH12`` — OCR merged the same state+RTO line twice.
    parts = _STD_PARTS.match(compact)
    if parts and parts.group(2) == compact[:2]:
        return False
    # ``TN51YTN51`` / ``TN51YTN513`` — series ends with the state code (left-half
    # echo into the series field).
    if parts:
        state = compact[:2]
        series = parts.group(2)
        if len(series) >= 3 and series.endswith(state):
            return False
        # 1-digit RTO only allowed for Delhi legacy; other states need 2 digits.
        if len(parts.group(1)) == 1 and state != "DL":
            return False
    return True


def stitch_plate_fragments(*parts: str) -> str | None:
    """Try ordered concatenations of OCR fragments into one Indian plate.

    Used when a detector splits a plate into pieces (e.g. ``TN 51`` + ``Y 6552``)
    or an HSRP legend is cropped apart from the registration field.
    Two-line motorcycle plates often yield ``MH02G`` + ``D7249``.
    Returns the first compact string that matches a known Indian pattern.
    """
    cleaned: list[str] = []
    for part in parts:
        # Prefer the best embedded plate inside a multi-token OCR blob
        # (``MH12 AB 5687`` / ``MH12 MH12 AB 5687`` → ``MH12AB5687``).
        compact = sanitize_plate_text(part)
        if not compact:
            continue
        if matches_indian_plate(compact):
            return compact
        # Also try raw strip without sanitize early-exit for short tokens.
        raw_compact = strip_plate(part)
        if raw_compact and matches_indian_plate(raw_compact):
            return raw_compact
        # Right-half crops sometimes leak the district: ``51Y6552`` → ``Y6552``.
        leaked = re.match(r"^[0-9]{1,2}([A-Z]{1,3}[0-9]{1,4})$", raw_compact or "")
        if leaked:
            compact = leaked.group(1)
        if is_non_plate_text(compact):
            # Keep short stitch pieces: state/RTO (``MH`` / ``12``), series letters
            # (``Y`` / ``AB``), and digit suffixes (``5687``). Tokenized two-line OCR
            # (``MH``+``12``+``AB``+``5687``) must not drop the series field.
            if is_plate_marker_noise(compact) or compact in _WATERMARK_WORDS:
                continue
            if compact.isdigit() and 1 <= len(compact) <= 4:
                pass
            elif compact.isalpha() and 1 <= len(compact) <= 3:
                pass
            elif not (len(compact) <= 2 and (compact.isdigit() or compact.isalpha())):
                continue
        if compact not in cleaned:
            cleaned.append(compact)
    if len(cleaned) < 2:
        return None

    # Drop fragments that are strict prefixes of another (avoid MH12+MH12AB5687
    # inventing MH12MH12 via naive join when sanitize already failed).
    pruned: list[str] = []
    for a in cleaned:
        if any(b != a and b.startswith(a) and matches_indian_plate(b) for b in cleaned):
            continue
        pruned.append(a)
    cleaned = pruned or cleaned
    if len(cleaned) < 2:
        only = cleaned[0] if cleaned else None
        return only if only and matches_indian_plate(only) else None

    from itertools import permutations

    trials: list[tuple[str, ...]] = [tuple(cleaned)]
    for i, a in enumerate(cleaned):
        for j, b in enumerate(cleaned):
            if i == j:
                continue
            # Skip pure duplicated state+RTO joins (MH12+MH12).
            if a == b and re.match(r"^[A-Z]{2}[0-9]{1,2}$", a):
                continue
            pair = (a, b)
            if pair not in trials:
                trials.append(pair)
    max_tok = max((len(c) for c in cleaned), default=0)
    if len(cleaned) <= 3 or (len(cleaned) == 4 and max_tok <= 5):
        for perm in permutations(cleaned):
            if perm not in trials:
                trials.append(perm)
    elif len(cleaned) <= 6:
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
        candidates = [joined, sanitize_plate_text(joined)]
        stripped = joined
        while stripped.startswith("IND") and len(stripped) > 3:
            stripped = stripped[3:]
        if stripped != joined:
            candidates.append(stripped)
        for cand in candidates:
            if not cand or not matches_indian_plate(cand):
                continue
            score = float(len(cand))
            if cand[:2] in _INDIAN_STATE_CODES:
                score += 40.0
            m = _STD_PARTS.match(cand)
            if m:
                if len(m.group(1)) == 2:
                    score += 20.0
                if len(m.group(2)) >= 2:
                    score += 10.0
                if m.group(2) in _INDIAN_STATE_CODES:
                    score -= 55.0
                # Doubled series letters (``YY`` from TN51Y+Y6552) are OCR artifacts.
                if re.search(r"(.)\1", m.group(2)):
                    score -= 30.0
                suffix_len = len(m.group(3))
                if 1 <= suffix_len <= 4:
                    score += 5.0 * suffix_len
            # Prefer simple two-fragment joins (TN51+Y6552) over noise-inflated triples.
            score += sum(len(p) for p in trial) * 0.25
            score -= max(0, len(trial) - 2) * 18.0
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
    elif match[:2] in _INDIAN_STATE_CODES and (
        INDIAN_STANDARD.match(match) or INDIAN_STANDARD_DL_1RTO.match(match)
    ):
        score += 40.0
    elif INDIAN_STANDARD.match(match) or INDIAN_STANDARD_DL_1RTO.match(match):
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
        if len(series) >= 3:
            # Prefer full series over digit-bleed truncations (CBD0010 vs CB0001).
            score += 14.0
        # ``MH12MH12`` — series equals a state code (duplicated top line). Prefer
        # longer real plates like ``MH12AB5687`` from the same OCR blob.
        if series in _INDIAN_STATE_CODES:
            score -= 55.0
        # Slight preference for longer suffixes without inventing digits.
        score += min(len(tail), 4) * 2.0
        if len(tail) == 4:
            score += 6.0
    return score


def _bh_year_stolen_from_number(compact: str, start: int, match: str) -> bool:
    """True when YY was sliced off the unique number sitting just before ``BH``.

    OCR ``6517 BH 6517 TA`` compactifies to ``6517BH6517TA``; a naive finder
    reports ``17BH6517TA`` (year stolen from ``6517``). Real years sit *before*
    an independent four-digit unique number.
    """
    if not INDIAN_BHARAT.match(match) or start < 2:
        return False
    year, number = match[:2], match[4:8]
    # Four digits ending at the year: …6517 BH 6517…
    four = compact[start - 2 : start + 2]
    return four == number and four.endswith(year)


def _find_embedded_plates(compact: str) -> list[str]:
    """Collect BH/standard plate substrings, including overlapping matches.

    Non-overlapping ``finditer`` alone keeps ``MH12MH12`` from
    ``MH12MH12AB5687`` and never sees the better ``MH12AB5687``.
    """
    hits: list[str] = []
    for rx in (_BH_FIND, _STD_FIND, _STD_FIND_DL):
        for i in range(len(compact)):
            m = rx.match(compact, i)
            if m is not None and m.start() == i:
                text = m.group(0)
                if rx is _BH_FIND and _bh_year_stolen_from_number(compact, i, text):
                    continue
                hits.append(text)
    return hits


def extract_all_indian_plates(value: str) -> list[str]:
    """Return every distinct valid Indian plate embedded in an OCR blob.

    Multi-vehicle crops sometimes OCR as ``MH12AB5687 MH05EK3142`` — sanitize
    alone keeps only the best one; callers that want *all* detections use this.
    """
    compact = strip_plate(value)
    if not compact:
        return []
    spans: list[tuple[int, int, str]] = []
    for i in range(len(compact)):
        for rx in (_BH_FIND, _STD_FIND, _STD_FIND_DL):
            m = rx.match(compact, i)
            if m is None or m.start() != i:
                continue
            text = m.group(0)
            if rx is _BH_FIND and _bh_year_stolen_from_number(compact, i, text):
                continue
            if matches_indian_plate(text):
                spans.append((m.start(), m.end(), text))
    if not spans:
        only = sanitize_plate_text(value)
        return [only] if only and matches_indian_plate(only) else []
    # Prefer longer spans, then left-to-right; keep non-overlapping.
    spans.sort(key=lambda s: (-(s[1] - s[0]), s[0]))
    used = [False] * len(compact)
    chosen: list[tuple[int, str]] = []
    for start, end, text in spans:
        if any(used[start:end]):
            continue
        for j in range(start, end):
            used[j] = True
        chosen.append((start, text))
    chosen.sort(key=lambda item: item[0])
    out: list[str] = []
    for _, text in chosen:
        if text not in out:
            out.append(text)
    return out


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
    # Collect all BH + standard hits (overlapping); prefer longest / complete forms.
    candidates: list[str] = list(_find_embedded_plates(compact))
    if "IND" in compact:
        without_ind = compact.replace("IND", "")
        if without_ind:
            candidates.append(without_ind)
            candidates.extend(_find_embedded_plates(without_ind))
    # Whole compact may already be a valid plate (prefer over shorter embeds).
    if matches_indian_plate(compact):
        candidates.append(compact)
    # Slot-aware repair: digit bleed into series (``DL3CB00010`` → ``DL3CBD0010``)
    # must beat truncated embeds like ``DL3CB0001``.
    for source in (compact, *(candidates[:8])):
        repaired = slot_aware_repair_standard(source)
        if repaired:
            candidates.append(repaired)
    # Also repair after dropping trailing non-plate tokens (``…0010NO`` / ``…0010IND``).
    trimmed = re.sub(r"(NO|IND|INDIA)$", "", compact)
    if trimmed and trimmed != compact:
        repaired = slot_aware_repair_standard(trimmed)
        if repaired:
            candidates.append(repaired)
        candidates.extend(_find_embedded_plates(trimmed))
    if candidates:
        uniq: list[str] = []
        for c in candidates:
            if c not in uniq:
                uniq.append(c)
        # Prefer real Indian plates over shape-only false joins (MH12MH12).
        patterned = [c for c in uniq if matches_indian_plate(c)]
        pool = patterned or uniq
        return max(pool, key=_score_embedded_plate)
    return compact


def _digit_slot_indexes(compact: str) -> set[int]:
    """Indexes that should hold digits for standard / Bharat Indian plates."""
    chars = list(compact)
    if not chars:
        return set()
    if INDIAN_BHARAT.match(compact) or (len(chars) >= 4 and chars[2:4] == ["B", "H"]):
        return {0, 1, 4, 5, 6, 7}
    return {2, 3} | set(range(max(0, len(chars) - 4), len(chars)))


def chars_slot_compatible(a: str, b: str) -> bool:
    """True when two characters are equal or a documented slot ambiguity pair."""
    if a == b:
        return True
    pair = frozenset({a, b})
    return any(pair <= group for group in _SLOT_AMBIGUITY_GROUPS)


def plates_slot_equivalent(a: str, b: str) -> bool:
    """True when two plate strings match under slot-aware D/0 B/8 O/0 I/1 S/5 ambiguity."""
    ca, cb = strip_plate(a), strip_plate(b)
    if ca == cb:
        return True
    if not ca or not cb or len(ca) != len(cb):
        return False
    for x, y in zip(ca, cb):
        if not chars_slot_compatible(x, y):
            return False
    return True


# State/UT slot letter confusions only (OCR glyph pairs). Never used for a
# global H→M replace — only when the first two chars are an *invalid* state
# code and the remaining slots already form a strong civilian structure.
_STATE_LETTER_CONFUSIONS: dict[str, frozenset[str]] = {
    "H": frozenset({"M", "N", "R", "A", "K"}),
    "M": frozenset({"H", "N", "W"}),
    "N": frozenset({"H", "M", "W"}),
    "R": frozenset({"H", "P", "K", "B"}),
    "P": frozenset({"R", "F", "B"}),
    "B": frozenset({"R", "P", "G", "8"}),
    "G": frozenset({"C", "O", "Q", "B"}),
    "C": frozenset({"G", "O", "Q"}),
    "O": frozenset({"D", "Q", "G", "C", "0"}),
    "D": frozenset({"O", "0", "Q"}),
    "A": frozenset({"H", "R"}),
    "K": frozenset({"X", "R", "H"}),
    "X": frozenset({"K", "Y"}),
    "W": frozenset({"M", "N", "V"}),
    "V": frozenset({"W", "U", "Y"}),
    "U": frozenset({"V", "O"}),
    "F": frozenset({"P", "E"}),
    "E": frozenset({"F", "B"}),
    "T": frozenset({"Y", "I"}),
    "Y": frozenset({"T", "V", "X"}),
    "L": frozenset({"I", "T"}),
    "I": frozenset({"L", "T", "J", "1"}),
    "J": frozenset({"I", "T"}),
    "S": frozenset({"5", "B"}),
    "Z": frozenset({"2"}),
}

# Higher = more common OCR swap for state letters (H↔M dominates high-mount CCTV).
_STATE_CONFUSION_WEIGHT: dict[tuple[str, str], float] = {
    ("H", "M"): 1.00,
    ("M", "H"): 1.00,
    ("H", "N"): 0.70,
    ("N", "H"): 0.70,
    ("M", "N"): 0.65,
    ("N", "M"): 0.65,
    ("H", "R"): 0.55,
    ("R", "H"): 0.55,
    ("H", "A"): 0.45,
    ("A", "H"): 0.45,
    ("H", "K"): 0.40,
    ("K", "H"): 0.40,
    ("D", "O"): 0.85,
    ("O", "D"): 0.85,
    ("O", "0"): 0.80,
    ("0", "O"): 0.80,
    ("D", "0"): 0.75,
    ("0", "D"): 0.75,
    ("G", "C"): 0.60,
    ("C", "G"): 0.60,
    ("P", "R"): 0.55,
    ("R", "P"): 0.55,
    ("B", "R"): 0.50,
    ("R", "B"): 0.50,
    ("K", "X"): 0.50,
    ("X", "K"): 0.50,
    ("M", "W"): 0.45,
    ("W", "M"): 0.45,
    ("I", "L"): 0.55,
    ("L", "I"): 0.55,
    ("T", "Y"): 0.50,
    ("Y", "T"): 0.50,
    ("V", "U"): 0.45,
    ("U", "V"): 0.45,
    ("S", "5"): 0.70,
    ("5", "S"): 0.70,
}

# Rest-of-plate after state: exactly 2 RTO digits + series + unique number.
_STRONG_CIVILIAN_REST = re.compile(r"^([0-9]{2})([A-Z]{1,3})([0-9]{1,4})$")

# Letter-slot alternatives for glyph confusables (prefer D over O for ``0``).
_LETTER_SLOT_ALTS: dict[str, tuple[str, ...]] = {
    "0": ("D", "O"),
    "1": ("I",),
    "5": ("S",),
    "8": ("B",),
    "O": ("O",),
    "I": ("I",),
    "S": ("S",),
    "B": ("B",),
    "D": ("D",),
}
_DIGIT_SLOT_ALTS: dict[str, tuple[str, ...]] = {
    "O": ("0",),
    "D": ("0",),
    "I": ("1",),
    "Z": ("2",),
    "S": ("5",),
    "B": ("8",),
    "0": ("0",),
    "1": ("1",),
    "2": ("2",),
    "5": ("5",),
    "8": ("8",),
}


def _repair_field_chars(field: str, *, as_digits: bool) -> list[str]:
    """Expand a field into confusable-consistent alternatives (bounded)."""
    alts_map = _DIGIT_SLOT_ALTS if as_digits else _LETTER_SLOT_ALTS
    options: list[str] = [""]
    for ch in field:
        next_opts: list[str] = []
        choices = alts_map.get(ch)
        if choices is None:
            # Already the right alphabet, or unknown — keep as-is only if compatible.
            if as_digits and ch.isdigit():
                choices = (ch,)
            elif (not as_digits) and ch.isalpha():
                choices = (ch,)
            else:
                return []
        for prefix in options:
            for c in choices:
                next_opts.append(prefix + c)
                if len(next_opts) > 24:
                    break
            if len(next_opts) > 24:
                break
        options = next_opts
        if not options:
            return []
    # Digits field must be all digits; series all letters.
    if as_digits:
        return [o for o in options if o.isdigit()]
    return [o for o in options if o.isalpha()]


def _state_confusion_weight(ocr_ch: str, cand_ch: str) -> float | None:
    """Weight for a single state-slot OCR→candidate letter swap, or None if disallowed."""
    if ocr_ch == cand_ch:
        return 1.0
    alts = _STATE_LETTER_CONFUSIONS.get(ocr_ch)
    if not alts or cand_ch not in alts:
        return None
    return _STATE_CONFUSION_WEIGHT.get((ocr_ch, cand_ch), 0.35)


def reconcile_state_prefix(value: str) -> str | None:
    """Correct an invalid OCR state/UT prefix when the remaining slots are strong.

    Example: ``HH12VF8354`` → ``MH12VF8354`` (H↔M on state slot only).

    Constraints:
    - First two chars must *not* already be a known state/UT code.
    - Remaining text must be exactly ``DD + series(1–3) + number(1–4)``.
    - At most **one** state-slot character may change, and only via documented
      confusions (never a global H→M replace, never arbitrary garbage).
    - Corrected prefix must be a known Indian state/UT; full plate must pass
      ``matches_indian_plate`` (rejects ``MH2F22`` / ``MN2F2`` style shapes).
    - Ambiguous top candidates (close confusion weights) are rejected.
    """
    compact = strip_plate(value)
    if not compact or len(compact) < 8 or len(compact) > 12:
        return None
    if matches_indian_plate(compact):
        return compact
    state, rest = compact[:2], compact[2:]
    if not state.isalpha() or state in _INDIAN_STATE_CODES:
        return None
    rest_m = _STRONG_CIVILIAN_REST.match(rest)
    if not rest_m:
        return None
    series = rest_m.group(2)
    # Series must not echo a state code (duplicated top-line artifact).
    if series in _INDIAN_STATE_CODES:
        return None

    scored: list[tuple[float, str]] = []
    for code in _INDIAN_STATE_CODES:
        diffs = [(i, state[i], code[i]) for i in range(2) if state[i] != code[i]]
        if len(diffs) != 1:
            # Zero diffs impossible (state invalid); >1 edit = too far.
            continue
        _, ocr_ch, cand_ch = diffs[0]
        weight = _state_confusion_weight(ocr_ch, cand_ch)
        if weight is None:
            continue
        cand = f"{code}{rest}"
        if not matches_indian_plate(cand):
            continue
        # Prefer stronger confusions and fuller series/number fields.
        score = 100.0 * weight
        score += 6.0 * len(series)
        score += 3.0 * len(rest_m.group(3))
        scored.append((score, cand))

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best = scored[0]
    # Require a clear winner — do not guess among near-tied state codes.
    if len(scored) > 1 and best_score - scored[1][0] < 8.0:
        return None
    # Absolute floor: at least a moderately common confusion (e.g. H↔R alone
    # without margin would already fail the tie check against H↔M).
    if best_score < 45.0:
        return None
    return best


def is_near_pattern_invalid_state(value: str) -> bool:
    """True when OCR has strong civilian structure but an invalid state prefix.

    Used by the ensemble to prefer a cheap state-prefix reconcile over more
    expensive preprocess variants / ``abandoned_no_pattern``.
    """
    compact = strip_plate(value)
    if not compact or len(compact) < 8:
        return False
    if matches_indian_plate(compact):
        return False
    state, rest = compact[:2], compact[2:]
    if not state.isalpha() or state in _INDIAN_STATE_CODES:
        return False
    return _STRONG_CIVILIAN_REST.match(rest) is not None


def slot_aware_repair_standard(compact: str) -> str | None:
    """Reinterpret SS + RTO + series letters + digits using slot confusables.

    Fixes digit bleed into the series field (``DL3CB00010`` → ``DL3CBD0010``)
    and invalid-state near-plates (``HH12VF8354`` → ``MH12VF8354``) without
    blind global O/0 or H→M substitution. Returns the best pattern-valid repair
    or ``None`` when no structural interpretation works.
    """
    compact = strip_plate(compact)
    if not compact or len(compact) < 7 or len(compact) > 12:
        return None
    if matches_indian_plate(compact):
        return compact

    # Invalid state + strong rest (HH12VF8354) before broader field repair.
    reconciled = reconcile_state_prefix(compact)
    if reconciled and matches_indian_plate(reconciled):
        return reconciled

    scored: list[tuple[float, str]] = []

    def _consider(state: str, rest: str, *, state_flips: int) -> None:
        if state not in _INDIAN_STATE_CODES:
            return
        # Doubled top-line OCR (``MH12MH12``) must not become ``MH12MHI2``.
        if re.match(rf"^{re.escape(state)}[0-9]{{1,2}}{re.escape(state)}", state + rest):
            return
        for dist_len in (1, 2):
            for series_len in (1, 2, 3):
                num_len = len(rest) - dist_len - series_len
                if num_len < 1 or num_len > 4:
                    continue
                dist_raw = rest[:dist_len]
                series_raw = rest[dist_len : dist_len + series_len]
                num_raw = rest[dist_len + series_len :]
                dist_opts = _repair_field_chars(dist_raw, as_digits=True)
                series_opts = _repair_field_chars(series_raw, as_digits=False)
                num_opts = _repair_field_chars(num_raw, as_digits=True)
                if not dist_opts or not series_opts or not num_opts:
                    continue
                for dist in dist_opts:
                    for series in series_opts:
                        # ``MH12MH…`` → series starting with the same state code is an echo.
                        if len(series) >= 2 and series[:2] == state:
                            continue
                        if series in _INDIAN_STATE_CODES:
                            continue
                        for number in num_opts:
                            cand = f"{state}{dist}{series}{number}"
                            if not matches_indian_plate(cand):
                                continue
                            flips = state_flips + sum(
                                1
                                for a, b in zip(
                                    dist_raw + series_raw + num_raw,
                                    dist + series + number,
                                )
                                if a != b
                            )
                            score = 100.0 - 12.0 * flips
                            score += 8.0 * len(series)
                            score += 5.0 * len(number)
                            if len(dist) == 2:
                                score += 6.0
                            scored.append((score, cand))

    # Normal path: first two chars are the state code (possibly already letters).
    _consider(compact[:2], compact[2:], state_flips=0)
    # Leading digit bleed into state (``0L8CAL0413`` → ``DL8CAL0413``).
    if compact[0].isdigit() or compact[1].isdigit():
        for state in _repair_field_chars(compact[:2], as_digits=False):
            flips = sum(1 for a, b in zip(compact[:2], state) if a != b)
            _consider(state, compact[2:], state_flips=flips)

    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _apply_confusables(compact: str) -> str:
    if matches_indian_plate(compact):
        return compact
    repaired = slot_aware_repair_standard(compact)
    if repaired and matches_indian_plate(repaired):
        return repaired
    chars = list(compact)
    if len(chars) < 8:
        return compact
    candidate = chars[:]
    digit_idxs = _digit_slot_indexes("".join(candidate))
    for i, ch in enumerate(candidate):
        in_digit_slot = i in digit_idxs
        if in_digit_slot and ch in DIGIT_CONFUSABLES:
            candidate[i] = DIGIT_CONFUSABLES[ch]
        elif not in_digit_slot and ch in LETTER_CONFUSABLES:
            candidate[i] = LETTER_CONFUSABLES[ch]
    joined = "".join(candidate)
    if matches_indian_plate(joined):
        return joined
    # Letter-slot 0 is often D (state ``DL``) rather than O — try D when O failed.
    if "0" in chars:
        alt = chars[:]
        for i, ch in enumerate(alt):
            if i not in digit_idxs and ch == "0":
                alt[i] = "D"
        alt_joined = "".join(alt)
        if matches_indian_plate(alt_joined):
            return alt_joined
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
