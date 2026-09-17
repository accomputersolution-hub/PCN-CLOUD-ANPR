from __future__ import annotations

"""Multi-pass OCR selection for Indian plates.

Does not invent missing characters. Prefers pattern matches, consistency, length,
and confidence. Separates raw OCR text from normalized output.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from pcn_anpr.interfaces import OCRProvider, OCRResult
from pcn_anpr.normalize import matches_indian_plate, normalize_plate, strip_plate
from pcn_anpr.preprocess import PreprocessVariant, save_debug_crops


@dataclass
class OcrPassResult:
    variant: str
    raw_text: str
    normalized: str
    confidence: float
    matches_pattern: bool


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


def _plausible_length(text: str) -> bool:
    n = len(strip_plate(text))
    # Indian standard ~8–11; Bharat similar
    return 7 <= n <= 12


def score_candidate(pass_result: OcrPassResult, consistency: int) -> float:
    """Higher is better. Never boost by fabricating characters."""
    score = 0.0
    if pass_result.matches_pattern:
        score += 1000.0
    if _plausible_length(pass_result.normalized):
        score += 50.0
    # Prefer longer plausible reads (helps recover truncated tails like …019)
    score += min(len(pass_result.normalized), 12) * 8.0
    score += float(pass_result.confidence) * 100.0
    score += consistency * 40.0
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

    counts = Counter(strip_plate(p.normalized) for p in passes if p.normalized)
    best: OcrPassResult | None = None
    best_score = float("-inf")
    for p in passes:
        consistency = counts.get(strip_plate(p.normalized), 0)
        s = score_candidate(p, consistency)
        if s > best_score:
            best_score = s
            best = p

    assert best is not None
    confident = bool(
        best.matches_pattern
        and best.confidence >= min_ocr_confidence
        and _plausible_length(best.normalized)
    )
    return EnsembleOcrResult(
        raw_text=best.raw_text,
        normalized_text=best.normalized if confident else None,
        ocr_confidence=float(best.confidence),
        ocr_confident=confident,
        matches_pattern=best.matches_pattern,
        passes=passes,
        selected_variant=best.variant,
    )


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
) -> EnsembleOcrResult:
    from pcn_anpr.preprocess import generate_live_ocr_variants, generate_ocr_variants

    if live_mode:
        variants = generate_live_ocr_variants(plate_crop_bgr)
    else:
        variants = generate_ocr_variants(plate_crop_bgr, scales=scales)
    if not variants and plate_crop_bgr is not None:
        variants = [PreprocessVariant("raw", plate_crop_bgr)]

    debug_paths: list[str] = []
    if debug_dir:
        debug_paths = save_debug_crops(variants, debug_dir, debug_prefix)

    passes: list[OcrPassResult] = []
    for v in variants:
        try:
            result: OCRResult = ocr.read(v.image)
        except Exception:
            continue
        raw = (result.raw_text or result.text or "").strip()
        if not raw:
            continue
        norm = normalize_plate(raw, confusable_substitution=confusable_substitution)
        passes.append(
            OcrPassResult(
                variant=v.name,
                raw_text=raw,
                normalized=norm.normalized,
                confidence=float(result.confidence or 0.0),
                matches_pattern=norm.matches_known_pattern,
            )
        )
        if (
            early_exit_on_confident
            and norm.matches_known_pattern
            and float(result.confidence or 0.0) >= min_ocr_confidence
            and _plausible_length(norm.normalized)
        ):
            break

    ensemble = select_best_pass(passes, min_ocr_confidence=min_ocr_confidence)
    ensemble.debug_crops = debug_paths
    return ensemble
