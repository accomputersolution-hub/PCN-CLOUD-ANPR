from __future__ import annotations

"""Multi-pass OCR selection for Indian plates.

Does not invent missing characters. Prefers pattern matches, consistency, length,
and confidence. Separates raw OCR text from normalized output.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from pcn_anpr.interfaces import OCRProvider, OCRResult
from pcn_anpr.normalize import (
    is_near_pattern_invalid_state,
    is_non_plate_text,
    is_plate_marker_noise,
    matches_indian_plate,
    normalize_plate,
    plates_slot_equivalent,
    reconcile_state_prefix,
    stitch_plate_fragments,
    strip_plate,
)
from pcn_anpr.preprocess import PreprocessVariant, save_debug_crops

# Cheap/high-value order for adaptive offline fast-path. CLAHE/deskew/persp before
# sharpen/color — low-res crops often misread D↔Y on sharpen alone.
_FAST_PATH_VARIANT_ORDER: tuple[str, ...] = (
    "gray_clahe_x2",
    "deskew_clahe_x2",
    "persp_clahe_x2",
    "sharpen_clahe_x2",
    "color_x2",
    "gray_clahe_x3",
    "deskew_clahe_x3",
    "persp_clahe_x3",
    "sharpen_clahe_x3",
    "color_x3",
    "adaptive_x2",
    "deskew_adaptive_x2",
    "adaptive_x3",
    "deskew_adaptive_x3",
)

# Strong early-exit (secondary / multi-vehicle): color/sharpen/adaptive first so a
# gray_clahe no-pattern fragment (e.g. ``0L413``) cannot starve the good variants.
_STRONG_PATH_VARIANT_ORDER: tuple[str, ...] = (
    "color_x2",
    "sharpen_clahe_x2",
    "adaptive_x2",
    "gray_clahe_x2",
    "deskew_clahe_x2",
    "persp_clahe_x2",
    "color_x3",
    "sharpen_clahe_x3",
    "adaptive_x3",
    "gray_clahe_x3",
    "deskew_clahe_x3",
    "persp_clahe_x3",
    "deskew_adaptive_x2",
    "deskew_adaptive_x3",
)
_STRONG_PRIORITY_VARIANTS: tuple[str, ...] = (
    "color_x2",
    "sharpen_clahe_x2",
    "adaptive_x2",
)
# Solo high-confidence full-plate exit (no rival pattern) under strong consensus.
_STRONG_SOLO_MIN_CONFIDENCE = 0.85


@dataclass
class OcrPassResult:
    variant: str
    raw_text: str
    normalized: str
    confidence: float
    matches_pattern: bool
    elapsed_ms: float = 0.0


@dataclass
class EnsembleOcrResult:
    raw_text: str
    normalized_text: str | None
    ocr_confidence: float
    ocr_confident: bool
    matches_pattern: bool
    passes: list[OcrPassResult] = field(default_factory=list)
    debug_crops: list[str] = field(default_factory=list)
    selected_variant: str | None = None
    timing: dict[str, Any] = field(default_factory=dict)


def _plausible_length(text: str) -> bool:
    n = len(strip_plate(text))
    # Standard with 1–4 digit suffix ≈ 7–11; Bharat similar
    return 7 <= n <= 12


def _standard_prefix_and_suffix(text: str) -> tuple[str, str] | None:
    """Return (state+district+series, digit_suffix) for a standard plate, else None."""
    import re

    compact = strip_plate(text)
    m = re.match(r"^([A-Z]{2}[0-9]{1,2}[A-Z]{1,3})([0-9]{1,4})$", compact)
    if not m:
        return None
    return m.group(1), m.group(2)


def score_candidate(pass_result: OcrPassResult, consistency: int) -> float:
    """Higher is better. Pattern match dominates OCR confidence.

    Non-plate OCR (``alamy``, ``IND``, brand words) is heavily penalized so it
    can never win solely on confidence.
    """
    score = 0.0
    if pass_result.matches_pattern:
        score += 1000.0
    elif is_non_plate_text(pass_result.normalized):
        score -= 900.0
    elif is_plate_marker_noise(pass_result.normalized):
        score -= 800.0
    else:
        # Incomplete / unknown OCR — keep below any pattern match.
        score -= 50.0
    if pass_result.matches_pattern and _plausible_length(pass_result.normalized):
        score += 50.0
    # Mild length preference — do NOT force a 4-digit unique number
    # (KL03AF786 must not lose to invented KL03AF8878 solely on length).
    if pass_result.matches_pattern:
        nlen = len(strip_plate(pass_result.normalized))
        if nlen >= 9:
            score += 12.0
        elif nlen == 8:
            score += 4.0
        elif nlen <= 7:
            score -= 8.0
        from pcn_anpr.normalize import _INDIAN_STATE_CODES

        if strip_plate(pass_result.normalized)[:2] in _INDIAN_STATE_CODES:
            score += 25.0
        parts = _standard_prefix_and_suffix(pass_result.normalized)
        if parts:
            _prefix, suffix = parts
            # Slight reward for any valid suffix length; no 4-digit mandate.
            score += min(len(suffix), 4) * 2.0
            # Prefer 3-letter series over digit-bleed truncations (CBD vs CB).
            import re as _re

            m = _re.match(r"^[A-Z]{2}[0-9]{1,2}([A-Z]{1,3})[0-9]{1,4}$", strip_plate(pass_result.normalized))
            if m and len(m.group(1)) >= 3:
                score += 18.0
            elif m and len(m.group(1)) == 2:
                score += 4.0
    # Prefer longer plausible reads among non-pattern incomplete OCR only.
    if not pass_result.matches_pattern and not is_non_plate_text(pass_result.normalized):
        score += min(len(pass_result.normalized), 12) * 8.0
    elif pass_result.matches_pattern:
        score += min(len(strip_plate(pass_result.normalized)), 12) * 3.0
    # Confidence only breaks ties among pattern-matching (or non-noise) reads.
    conf_weight = 100.0 if pass_result.matches_pattern else 10.0
    score += float(pass_result.confidence) * conf_weight
    # Consistency only helps when the text already matches an Indian pattern —
    # otherwise repeated truncations (``6552`` x3) beat fuller ``Y6552``.
    if pass_result.matches_pattern:
        score += consistency * 40.0
    else:
        score += min(consistency, 1) * 5.0
    return score


def select_best_pass(passes: list[OcrPassResult], *, min_ocr_confidence: float) -> EnsembleOcrResult:
    if not passes:
        return EnsembleOcrResult(
            raw_text="",
            normalized_text=None,
            ocr_confidence=0.0,
            ocr_confident=False,
            matches_pattern=False,
            passes=[],
        )

    # Prefer Indian-pattern candidates exclusively when any exist.
    pattern_passes = [p for p in passes if p.matches_pattern]
    candidate_pool = pattern_passes or [p for p in passes if not is_non_plate_text(p.normalized)] or passes

    counts = Counter(strip_plate(p.normalized) for p in candidate_pool if p.normalized)
    best: OcrPassResult | None = None
    best_score = float("-inf")
    for p in candidate_pool:
        consistency = counts.get(strip_plate(p.normalized), 0)
        s = score_candidate(p, consistency)
        if s > best_score:
            best_score = s
            best = p

    assert best is not None

    # When several pattern hits share state+district+series but disagree on the
    # numeric suffix (786 vs 8878), prefer the higher-consistency read — never
    # silently invent extra digits just because a longer suffix also matches.
    if pattern_passes and best.matches_pattern:
        best_parts = _standard_prefix_and_suffix(best.normalized)
        if best_parts:
            prefix, _ = best_parts
            same_prefix = []
            for p in pattern_passes:
                parts = _standard_prefix_and_suffix(p.normalized)
                if parts and parts[0] == prefix:
                    same_prefix.append(p)
            if len(same_prefix) >= 2:
                def _suffix_key(p: OcrPassResult) -> tuple[int, int, float, int]:
                    key = strip_plate(p.normalized)
                    parts = _standard_prefix_and_suffix(key)
                    suffix = parts[1] if parts else ""
                    # Penalize a 4-digit suffix when a conflicting shorter peer
                    # exists (786 vs 8878). Do NOT penalize when the shorter is a
                    # prefix of this suffix (MH01EP9 → MH01EP9019 truncation).
                    invent_penalty = 0
                    if len(suffix) >= 3:
                        for q in same_prefix:
                            q_key = strip_plate(q.normalized)
                            if q_key == key:
                                continue
                            q_parts = _standard_prefix_and_suffix(q_key)
                            if not q_parts:
                                continue
                            q_suf = q_parts[1]
                            if (
                                len(q_suf) < len(suffix)
                                and counts.get(q_key, 0) >= counts.get(key, 0)
                                and not suffix.startswith(q_suf)
                            ):
                                invent_penalty = 1
                                break
                    return (
                        counts.get(key, 0),
                        -invent_penalty,
                        float(p.confidence),
                        len(suffix),
                    )

                best = max(same_prefix, key=_suffix_key)

    # When pattern hits disagree only on digit-suffix glyphs (4↔6 etc.), prefer
    # higher consistency then confidence — never invent a digit that no pass read.
    if pattern_passes and best.matches_pattern:
        best_key = strip_plate(best.normalized)
        digit_rivals = []
        for p in pattern_passes:
            key = strip_plate(p.normalized)
            if key == best_key:
                digit_rivals.append(p)
                continue
            if len(key) != len(best_key):
                continue
            # Same length; only digit slots differ (no letter-slot changes).
            letter_diff = False
            digit_diff = False
            for a, b in zip(best_key, key):
                if a == b:
                    continue
                if a.isdigit() and b.isdigit():
                    digit_diff = True
                else:
                    letter_diff = True
                    break
            if not letter_diff and digit_diff:
                digit_rivals.append(p)
        if len(digit_rivals) >= 2:
            def _digit_key(p: OcrPassResult) -> tuple[int, float]:
                return (counts.get(strip_plate(p.normalized), 0), float(p.confidence))

            best = max(digit_rivals, key=_digit_key)

    confident = bool(
        best.matches_pattern
        and best.confidence >= min_ocr_confidence
        and _plausible_length(best.normalized)
        and not is_non_plate_text(best.normalized)
    )
    return EnsembleOcrResult(
        raw_text=best.raw_text,
        normalized_text=best.normalized if confident else None,
        ocr_confidence=float(best.confidence),
        ocr_confident=confident,
        matches_pattern=best.matches_pattern and not is_non_plate_text(best.normalized),
        passes=passes,
        selected_variant=best.variant,
    )


def order_variants_for_fast_path(variants: list[PreprocessVariant]) -> list[PreprocessVariant]:
    """Stable reorder: known high-value variants first, unknown names keep relative order at end."""
    rank = {name: i for i, name in enumerate(_FAST_PATH_VARIANT_ORDER)}
    indexed = list(enumerate(variants))
    indexed.sort(key=lambda item: (rank.get(item[1].name, 10_000), item[0]))
    return [v for _, v in indexed]


def order_variants_for_strong_path(variants: list[PreprocessVariant]) -> list[PreprocessVariant]:
    """Strong-mode reorder: color → sharpen → adaptive before CLAHE/deskew/persp."""
    rank = {name: i for i, name in enumerate(_STRONG_PATH_VARIANT_ORDER)}
    indexed = list(enumerate(variants))
    indexed.sort(key=lambda item: (rank.get(item[1].name, 10_000), item[0]))
    return [v for _, v in indexed]


def _slot_aware_pattern_counts(
    keys: list[str],
) -> Counter:
    """Count pattern keys merging slot-aware D/0 B/8 O/0 I/1 S/5 equivalents."""
    clusters: list[list[str]] = []
    for key in keys:
        placed = False
        for cluster in clusters:
            if plates_slot_equivalent(key, cluster[0]):
                cluster.append(key)
                placed = True
                break
        if not placed:
            clusters.append([key])
    counts: Counter = Counter()
    for cluster in clusters:
        rep = Counter(cluster).most_common(1)[0][0]
        counts[rep] = len(cluster)
    return counts


def _is_confident_pass(
    *,
    matches_pattern: bool,
    confidence: float,
    normalized: str,
    min_ocr_confidence: float,
) -> bool:
    if is_non_plate_text(normalized):
        return False
    return bool(
        matches_pattern
        and float(confidence or 0.0) >= min_ocr_confidence
        and _plausible_length(normalized)
    )


# After this many OCR attempts with zero Indian-pattern hits, abandon the crop
# under adaptive mode so other plate candidates can be tried cheaply.
_ADAPTIVE_ABANDON_AFTER_NO_PATTERN = 5
# Strong path: never abandon before the priority trio (color/sharpen/adaptive);
# after that, allow a couple more before giving up on empty bumper strips.
_STRONG_ABANDON_AFTER_NO_PATTERN = 5
# IND / watermark / English-only crops are dead ends — bail after fewer passes.
_ADAPTIVE_ABANDON_AFTER_NON_PLATE = 2

# --- Split OCR budgets (primary must never be starved by Stage-1 / background) ---
# Reserved exclusively for primary-ROI / two-line recovery (independent of Stage-1).
RESERVED_PRIMARY_OCR_CALLS = 12
MAX_PRIMARY_ROI_OCR_CALLS = RESERVED_PRIMARY_OCR_CALLS
# Stage-1 (incl. widen/HSRP) hard cap for Manual adaptive — leaves room for primary.
MAX_STAGE1_OCR_CALLS = 8
# Background / secondary vehicle OCR after primary is resolved (or failed).
MAX_SECONDARY_OCR_CALLS = 16
# Absolute ceiling = primary reserved + stage1 + secondary (+ small slack).
MAX_PADDLE_CALLS_HARD_CEILING = (
    RESERVED_PRIMARY_OCR_CALLS + MAX_STAGE1_OCR_CALLS + MAX_SECONDARY_OCR_CALLS + 4
)
# Soft per-crop budget alias (legacy tests / callers).
MAX_PADDLE_CALLS_PER_IMAGE = RESERVED_PRIMARY_OCR_CALLS


def _try_stitch_full_plate(passes: list[OcrPassResult]) -> OcrPassResult | None:
    """Build a synthetic pass when two-line fragments stitch into a full Indian plate."""
    if not passes:
        return None
    texts: list[str] = []
    token_texts: list[str] = []
    for p in passes:
        if p.raw_text:
            toks = [t.strip() for t in str(p.raw_text).replace("-", " ").split() if t.strip()]
            # Prefer space-separated tokens so "AB 5687 MH 12" is not locked as
            # the non-matching blob AB5687MH12 (which blocks 4-token reorder).
            if len(toks) >= 2:
                token_texts.extend(toks)
            else:
                texts.append(p.raw_text)
        if p.normalized:
            compact = strip_plate(p.normalized)
            if compact and matches_indian_plate(compact):
                texts.append(p.normalized)
            elif compact and len(compact) <= 10:
                # Always keep half-plate / swapped blobs (``MH12``, ``AB5687``,
                # ``AB5687MH12``) even when raw_text is space-tokenized.
                texts.append(p.normalized)
    stitched = None
    if token_texts:
        stitched = stitch_plate_fragments(*token_texts)
    if not stitched and texts:
        stitched = stitch_plate_fragments(*(token_texts + texts) if token_texts else texts)
    if not stitched and texts and token_texts:
        stitched = stitch_plate_fragments(*texts)
    if not stitched or not matches_indian_plate(stitched):
        return None
    if not _plausible_length(stitched):
        return None
    # Reject stitch that is only short junk joining (IU etc. already filtered by stitch).
    if is_non_plate_text(stitched):
        return None
    conf = max((float(p.confidence) for p in passes), default=0.0)
    # Prefer confidence from passes that contributed length to the stitch.
    contributing = [
        float(p.confidence)
        for p in passes
        if strip_plate(p.normalized) and strip_plate(p.normalized) in stitched
    ]
    if contributing:
        conf = max(conf, max(contributing))
    display = " ".join(dict.fromkeys(t for t in (token_texts + texts) if t))
    return OcrPassResult(
        variant="stitched_twoline",
        raw_text=display,
        normalized=stitched,
        confidence=conf,
        matches_pattern=True,
        elapsed_ms=0.0,
    )


def should_early_exit(
    passes: list[OcrPassResult],
    *,
    min_ocr_confidence: float,
    mode: str,
) -> bool:
    """Decide whether remaining OCR variants can be skipped.

    - ``single``: first confident Indian-pattern pass (live / legacy).
    - ``full_plate``: first high-confidence *full* Indian plate (or two-line stitch).
      Used for primary-ROI Manual ANPR — does not wait for a second variant.
    - ``consensus``: adaptive offline path — require the current best plate text to
      appear on >=2 confident pattern-matching passes so a single high-confidence
      misread (e.g. D→Y) cannot stop the multipass fallback early.
    - ``strong_consensus``: secondary / multi-vehicle strong path — require either
      ≥2 slot-aware agreeing strong pattern hits, or one high-confidence full plate
      with no conflicting pattern rival. Incomplete / no-pattern reads never exit.
    """
    if not passes:
        return False
    if mode == "single":
        last = passes[-1]
        return _is_confident_pass(
            matches_pattern=last.matches_pattern,
            confidence=last.confidence,
            normalized=last.normalized,
            min_ocr_confidence=min_ocr_confidence,
        )
    if mode == "full_plate":
        last = passes[-1]
        if _is_confident_pass(
            matches_pattern=last.matches_pattern,
            confidence=last.confidence,
            normalized=last.normalized,
            min_ocr_confidence=min_ocr_confidence,
        ):
            return True
        stitched = _try_stitch_full_plate(passes)
        if stitched is None:
            return False
        return _is_confident_pass(
            matches_pattern=True,
            confidence=stitched.confidence,
            normalized=stitched.normalized,
            min_ocr_confidence=min_ocr_confidence,
        )
    if mode == "strong_consensus":
        return _strong_consensus_ready(passes, min_ocr_confidence=min_ocr_confidence)
    if mode != "consensus":
        return False
    interim = select_best_pass(passes, min_ocr_confidence=min_ocr_confidence)
    if not interim.ocr_confident or not interim.normalized_text:
        return False
    key = strip_plate(interim.normalized_text)
    confident_pattern_keys = [
        strip_plate(p.normalized)
        for p in passes
        if p.matches_pattern
        and float(p.confidence) >= min_ocr_confidence
        and _plausible_length(p.normalized)
    ]
    counts = Counter(confident_pattern_keys)
    if counts.get(key, 0) < 2:
        return False
    # Require a clear plurality so D↔Y style conflicts keep running fallbacks.
    rival_max = max((c for k, c in counts.items() if k != key), default=0)
    return counts[key] > rival_max


def _strong_consensus_ready(
    passes: list[OcrPassResult],
    *,
    min_ocr_confidence: float,
) -> bool:
    """Strong-mode exit: ≥2 agreeing variants, or one solo high-conf with no rival."""
    confident = [
        p
        for p in passes
        if p.matches_pattern
        and float(p.confidence) >= min_ocr_confidence
        and _plausible_length(p.normalized)
        and not is_non_plate_text(p.normalized)
    ]
    if not confident:
        # Two-line stitch can still form a full plate from fragments.
        stitched = _try_stitch_full_plate(passes)
        if stitched is None:
            return False
        return _is_confident_pass(
            matches_pattern=True,
            confidence=stitched.confidence,
            normalized=stitched.normalized,
            min_ocr_confidence=max(min_ocr_confidence, _STRONG_SOLO_MIN_CONFIDENCE),
        )

    keys = [strip_plate(p.normalized) for p in confident]
    counts = _slot_aware_pattern_counts(keys)
    if not counts:
        return False
    best_key, best_n = counts.most_common(1)[0]
    rival_max = max((c for k, c in counts.items() if not plates_slot_equivalent(k, best_key)), default=0)

    # ≥2 slot-aware agreeing strong variants with clear plurality.
    if best_n >= 2 and best_n > rival_max:
        return True

    # One strong high-confidence full Indian plate and no conflicting strong result.
    if best_n == 1 and rival_max == 0:
        solo = next(p for p in confident if plates_slot_equivalent(p.normalized, best_key))
        solo_floor = max(float(min_ocr_confidence), _STRONG_SOLO_MIN_CONFIDENCE)
        return float(solo.confidence) >= solo_floor and _plausible_length(solo.normalized)

    return False


def _strong_sharpen_attempted(
    *,
    variants: list[PreprocessVariant],
    pass_timings: list[dict[str, Any]],
) -> bool:
    """True once sharpen_clahe_x2 has run (or was never planned)."""
    if not any(v.name == "sharpen_clahe_x2" for v in variants):
        return True
    return any(t.get("variant") == "sharpen_clahe_x2" for t in pass_timings)


def _strong_priority_exhausted(
    *,
    variants: list[PreprocessVariant],
    pass_timings: list[dict[str, Any]],
) -> bool:
    """True when color/sharpen/adaptive (if planned) have each been attempted."""
    planned = {v.name for v in variants}
    ran = {str(t.get("variant") or "") for t in pass_timings}
    for name in _STRONG_PRIORITY_VARIANTS:
        if name in planned and name not in ran:
            return False
    return True


def run_multipass_ocr(
    plate_crop_bgr: Any,
    ocr: OCRProvider,
    *,
    min_ocr_confidence: float = 0.3,
    confusable_substitution: bool = False,
    debug_dir: str | None = None,
    debug_prefix: str = "plate",
    scales: tuple[float, ...] = (2.0, 3.0),
    live_mode: bool = False,
    early_exit_on_confident: bool = False,
    adaptive_fast_path: bool = False,
    aggressive_early_exit: bool = False,
    strong_early_exit: bool = False,
) -> EnsembleOcrResult:
    import time

    from pcn_anpr.preprocess import generate_live_ocr_variants, generate_ocr_variants

    t_prep0 = time.perf_counter()
    # Multi-vehicle stage1/secondary: x2 only — x3 doubles planned variants with
    # little gain once strong_early_exit stops on early consensus.
    use_scales = scales
    if strong_early_exit and not live_mode and not aggressive_early_exit:
        use_scales = tuple(s for s in scales if float(s) <= 2.0) or (2.0,)
    if live_mode:
        variants = generate_live_ocr_variants(plate_crop_bgr)
    else:
        variants = generate_ocr_variants(plate_crop_bgr, scales=use_scales)
        if strong_early_exit and not aggressive_early_exit:
            variants = order_variants_for_strong_path(variants)
        elif adaptive_fast_path:
            variants = order_variants_for_fast_path(variants)
    if not variants and plate_crop_bgr is not None:
        variants = [PreprocessVariant("raw", plate_crop_bgr)]
    preprocess_ms = (time.perf_counter() - t_prep0) * 1000.0

    # Debug crops: keep all generated variants available when enabled. Preprocess is
    # cheap (~tens of ms); avoid this path entirely when debug_dir is unset.
    debug_paths: list[str] = []
    t_debug0 = time.perf_counter()
    if debug_dir:
        debug_paths = save_debug_crops(variants, debug_dir, debug_prefix)
    debug_crops_ms = (time.perf_counter() - t_debug0) * 1000.0 if debug_dir else 0.0

    # Primary-ROI Manual ANPR (aggressive): stop on first full confident Indian plate.
    # strong_early_exit alone: strong_consensus (multi-variant agreement / solo high-conf).
    # Stage-1 keeps adaptive consensus via strong_early_exit=False.
    if aggressive_early_exit:
        exit_mode = "full_plate"
    elif strong_early_exit:
        exit_mode = "strong_consensus"
    elif adaptive_fast_path and not live_mode:
        exit_mode = "consensus"
    elif early_exit_on_confident or adaptive_fast_path:
        exit_mode = "single"
    else:
        exit_mode = "none"

    passes: list[OcrPassResult] = []
    pass_timings: list[dict[str, Any]] = []
    early_exited = False
    abandon_reason: str | None = None
    early_exit_reason: str | None = None
    ocr_total_ms = 0.0

    from pcn_anpr import ocr_perf

    for v in variants:
        sess = ocr_perf.get_session()
        # Aggressive / primary-ROI path: reserved ROI budget is independent of
        # Stage-1 usage so background candidates cannot starve two-line recovery.
        if aggressive_early_exit and sess is not None and sess.enabled:
            roi_n = sum(
                1
                for c in sess.calls
                if str(getattr(c, "stage", "") or "").startswith("primary_roi")
            )
            if roi_n >= MAX_PRIMARY_ROI_OCR_CALLS:
                early_exited = True
                abandon_reason = "ocr_budget_exhausted"
                early_exit_reason = "ocr_budget_exhausted"
                break
            # Absolute ceiling only after primary reserved budget is already used.
            if len(sess.calls) >= MAX_PADDLE_CALLS_HARD_CEILING and roi_n >= RESERVED_PRIMARY_OCR_CALLS:
                early_exited = True
                abandon_reason = "ocr_budget_exhausted"
                early_exit_reason = "ocr_budget_exhausted"
                break
            # Skip duplicate fingerprint+variant — reuse is free (no Paddle call).
            skip_dup = False
            try:
                fp = ocr_perf.image_fingerprint(v.image)
                cached = sess.get_cached_ocr(fp, v.name)
                if cached is not None:
                    skip_dup = True
                    raw_c, conf_c = cached
                    if raw_c:
                        norm = normalize_plate(
                            raw_c, confusable_substitution=confusable_substitution
                        )
                        passes.append(
                            OcrPassResult(
                                variant=v.name,
                                raw_text=raw_c,
                                normalized=norm.normalized,
                                confidence=float(conf_c),
                                matches_pattern=norm.matches_known_pattern,
                                elapsed_ms=0.0,
                            )
                        )
                        pass_timings.append(
                            {
                                "variant": v.name,
                                "elapsed_ms": 0.0,
                                "ok": True,
                                "confidence": float(conf_c),
                                "normalized": norm.normalized,
                                "matches_pattern": norm.matches_known_pattern,
                                "duplicate_skip": True,
                            }
                        )
                        sess.note_duplicate_skip()
                    else:
                        pass_timings.append(
                            {
                                "variant": v.name,
                                "elapsed_ms": 0.0,
                                "ok": True,
                                "empty": True,
                                "duplicate_skip": True,
                            }
                        )
                        sess.note_duplicate_skip()
            except Exception:  # noqa: BLE001
                skip_dup = False
            if skip_dup:
                # Still allow stitch / early-exit checks on cached pass.
                if passes and exit_mode == "full_plate":
                    last = passes[-1]
                    if not (
                        last.matches_pattern and _plausible_length(last.normalized)
                    ):
                        stitched_pass = _try_stitch_full_plate(passes)
                        if stitched_pass is not None and _is_confident_pass(
                            matches_pattern=True,
                            confidence=stitched_pass.confidence,
                            normalized=stitched_pass.normalized,
                            min_ocr_confidence=min_ocr_confidence,
                        ):
                            passes.append(stitched_pass)
                            early_exited = True
                            early_exit_reason = "full_plate_stitched"
                            abandon_reason = None
                            break
                    if exit_mode != "none" and should_early_exit(
                        passes,
                        min_ocr_confidence=min_ocr_confidence,
                        mode=exit_mode,
                    ):
                        early_exited = True
                        early_exit_reason = (
                            "full_plate_confident"
                            if exit_mode == "full_plate"
                            else f"early_exit_{exit_mode}"
                        )
                        break
                continue
        elif sess is not None and sess.enabled:
            # Non-aggressive paths: skip exact duplicate fingerprint+variant too.
            try:
                fp = ocr_perf.image_fingerprint(v.image)
                cached = sess.get_cached_ocr(fp, v.name)
                if cached is not None:
                    sess.note_duplicate_skip()
                    raw_c, conf_c = cached
                    if raw_c:
                        norm = normalize_plate(
                            raw_c, confusable_substitution=confusable_substitution
                        )
                        passes.append(
                            OcrPassResult(
                                variant=v.name,
                                raw_text=raw_c,
                                normalized=norm.normalized,
                                confidence=float(conf_c),
                                matches_pattern=norm.matches_known_pattern,
                                elapsed_ms=0.0,
                            )
                        )
                    continue
            except Exception:  # noqa: BLE001
                pass
        t0 = time.perf_counter()
        try:
            with ocr_perf.ocr_variant_context(variant=v.name, image=v.image):
                result: OCRResult = ocr.read(v.image)
        except Exception:
            elapsed = (time.perf_counter() - t0) * 1000.0
            ocr_total_ms += elapsed
            pass_timings.append({"variant": v.name, "elapsed_ms": round(elapsed, 2), "ok": False})
            result = None  # type: ignore[assignment]
        else:
            elapsed = (time.perf_counter() - t0) * 1000.0
            ocr_total_ms += elapsed
            raw = (result.raw_text or result.text or "").strip()
            if sess is not None and sess.enabled:
                try:
                    fp = ocr_perf.image_fingerprint(v.image)
                    sess.cache_ocr(fp, v.name, raw, float(result.confidence or 0.0))
                except Exception:  # noqa: BLE001
                    pass
            if not raw:
                pass_timings.append(
                    {
                        "variant": v.name,
                        "elapsed_ms": round(elapsed, 2),
                        "ok": True,
                        "empty": True,
                        "confidence": float(result.confidence or 0.0),
                    }
                )
            else:
                norm = normalize_plate(raw, confusable_substitution=confusable_substitution)
                passes.append(
                    OcrPassResult(
                        variant=v.name,
                        raw_text=raw,
                        normalized=norm.normalized,
                        confidence=float(result.confidence or 0.0),
                        matches_pattern=norm.matches_known_pattern,
                        elapsed_ms=round(elapsed, 2),
                    )
                )
                pass_timings.append(
                    {
                        "variant": v.name,
                        "elapsed_ms": round(elapsed, 2),
                        "ok": True,
                        "confidence": float(result.confidence or 0.0),
                        "normalized": norm.normalized,
                        "matches_pattern": norm.matches_known_pattern,
                    }
                )
                # Two-line fragments → promote stitched full plate into the pass list.
                if exit_mode in ("full_plate", "strong_consensus") and not (
                    norm.matches_known_pattern and _plausible_length(norm.normalized)
                ):
                    stitched_pass = _try_stitch_full_plate(passes)
                    stitch_floor = (
                        max(min_ocr_confidence, _STRONG_SOLO_MIN_CONFIDENCE)
                        if exit_mode == "strong_consensus"
                        else min_ocr_confidence
                    )
                    already_full = any(
                        p.matches_pattern and _plausible_length(p.normalized)
                        for p in passes
                    )
                    if (
                        stitched_pass is not None
                        and not already_full
                        and _is_confident_pass(
                            matches_pattern=True,
                            confidence=stitched_pass.confidence,
                            normalized=stitched_pass.normalized,
                            min_ocr_confidence=stitch_floor,
                        )
                    ):
                        # Strong mode: still require sharpen before locking a stitch.
                        if exit_mode != "strong_consensus" or _strong_sharpen_attempted(
                            variants=variants, pass_timings=pass_timings
                        ):
                            passes.append(stitched_pass)
                            early_exited = True
                            early_exit_reason = "full_plate_stitched"
                            abandon_reason = None
                            break
                can_exit = exit_mode != "none" and should_early_exit(
                    passes,
                    min_ocr_confidence=min_ocr_confidence,
                    mode=exit_mode,
                )
                if (
                    can_exit
                    and exit_mode == "strong_consensus"
                    and not _strong_sharpen_attempted(
                        variants=variants, pass_timings=pass_timings
                    )
                ):
                    can_exit = False
                if can_exit:
                    early_exited = True
                    early_exit_reason = (
                        "full_plate_confident"
                        if exit_mode == "full_plate"
                        else (
                            "strong_consensus"
                            if exit_mode == "strong_consensus"
                            else f"early_exit_{exit_mode}"
                        )
                    )
                    break
                # Full-plate mode: state+RTO-only (MH12) is a PARTIAL — stop more
                # variants on this crop so the pipeline can run two-line recovery.
                if (
                    exit_mode == "full_plate"
                    and len(passes) >= 2
                    and not any(p.matches_pattern and _plausible_length(p.normalized) for p in passes)
                ):
                    import re as _re

                    last_c = strip_plate(passes[-1].normalized)
                    if _re.match(r"^[A-Z]{2}[0-9]{1,2}$", last_c or ""):
                        early_exited = True
                        early_exit_reason = "primary_partial_state_rto"
                        abandon_reason = None
                        break

            # Empty OCR after a strong-mode consensus candidate: still allow exit
            # once sharpen_clahe_x2 has been attempted (may itself be empty).
            if (
                not raw
                and exit_mode == "strong_consensus"
                and _strong_sharpen_attempted(variants=variants, pass_timings=pass_timings)
                and should_early_exit(
                    passes,
                    min_ocr_confidence=min_ocr_confidence,
                    mode=exit_mode,
                )
            ):
                early_exited = True
                early_exit_reason = "strong_consensus"
                break

        abandon_after = (
            _STRONG_ABANDON_AFTER_NO_PATTERN
            if strong_early_exit
            else _ADAPTIVE_ABANDON_AFTER_NO_PATTERN
        )
        # Strong mode: never abandon before color/sharpen/adaptive have each run.
        strong_ready_to_abandon = (not strong_early_exit) or _strong_priority_exhausted(
            variants=variants, pass_timings=pass_timings
        )
        if (
            (adaptive_fast_path or strong_early_exit)
            and not live_mode
            and strong_ready_to_abandon
            and len(pass_timings) >= abandon_after
            and not any(p.matches_pattern for p in passes)
        ):
            # Targeted state-prefix reconcile (HH12VF8354→MH12VF8354) before
            # abandoning / burning more expensive variants.
            rescued = False
            for p in passes:
                src = p.normalized or p.raw_text or ""
                if not is_near_pattern_invalid_state(src):
                    continue
                fixed = reconcile_state_prefix(src)
                if not fixed or not matches_indian_plate(fixed):
                    continue
                passes.append(
                    OcrPassResult(
                        variant=f"{p.variant}+state_prefix",
                        raw_text=p.raw_text,
                        normalized=fixed,
                        confidence=float(p.confidence),
                        matches_pattern=True,
                        elapsed_ms=0.0,
                    )
                )
                rescued = True
                break
            if rescued:
                early_exited = True
                early_exit_reason = "state_prefix_reconciled"
                abandon_reason = None
                break
            # Wrong plate crop / no usable text — skip remaining expensive variants.
            early_exited = True
            abandon_reason = "abandoned_no_pattern"
            early_exit_reason = abandon_reason
            break
        if (
            (adaptive_fast_path or strong_early_exit)
            and not live_mode
            and len(passes) >= _ADAPTIVE_ABANDON_AFTER_NON_PLATE
            and passes
            and all(is_non_plate_text(p.normalized) for p in passes)
            and not any(p.matches_pattern for p in passes)
        ):
            # Strong: still finish the priority trio before treating as non-plate.
            if strong_early_exit and not _strong_priority_exhausted(
                variants=variants, pass_timings=pass_timings
            ):
                pass
            else:
                early_exited = True
                abandon_reason = "abandoned_non_plate_text"
                early_exit_reason = abandon_reason
                break

    ensemble = select_best_pass(passes, min_ocr_confidence=min_ocr_confidence)
    # If stitch created a full plate but select_best didn't pick it as confident,
    # prefer the stitched synthetic pass when present.
    if (
        exit_mode in ("full_plate", "strong_consensus")
        and not ensemble.ocr_confident
        and any(p.variant == "stitched_twoline" for p in passes)
    ):
        stitch_pass = next(p for p in passes if p.variant == "stitched_twoline")
        stitch_floor = (
            max(min_ocr_confidence, _STRONG_SOLO_MIN_CONFIDENCE)
            if exit_mode == "strong_consensus"
            else min_ocr_confidence
        )
        if _is_confident_pass(
            matches_pattern=True,
            confidence=stitch_pass.confidence,
            normalized=stitch_pass.normalized,
            min_ocr_confidence=stitch_floor,
        ):
            ensemble = EnsembleOcrResult(
                raw_text=stitch_pass.raw_text,
                normalized_text=stitch_pass.normalized,
                ocr_confidence=float(stitch_pass.confidence),
                ocr_confident=True,
                matches_pattern=True,
                passes=passes,
                selected_variant=stitch_pass.variant,
            )
    ensemble.debug_crops = debug_paths
    ensemble.timing = {
        "preprocess_ms": round(preprocess_ms, 2),
        "debug_crops_ms": round(debug_crops_ms, 2),
        "ocr_total_ms": round(ocr_total_ms, 2),
        "variants_total": len(variants),
        "variants_run": len(pass_timings),
        "early_exited": early_exited,
        "early_exit_mode": exit_mode,
        "early_exit_reason": early_exit_reason,
        "abandon_reason": abandon_reason,
        "adaptive_fast_path": adaptive_fast_path,
        "aggressive_early_exit": aggressive_early_exit,
        "strong_early_exit": strong_early_exit,
        "pass_timings": pass_timings,
    }
    ocr_perf.note_ensemble_stats(
        variants_planned=len(variants),
        variants_run=len(pass_timings),
        early_exited=early_exited,
        early_exit_mode=exit_mode,
        abandon_reason=abandon_reason or early_exit_reason,
    )
    return ensemble
