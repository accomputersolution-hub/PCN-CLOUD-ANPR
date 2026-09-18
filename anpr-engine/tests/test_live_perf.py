from __future__ import annotations

"""ANPR engine reuse + live performance checks (no real camera required)."""

import json
import time
from pathlib import Path

import numpy as np
import pytest

from pcn_anpr.config import ANPRSettings
from pcn_anpr.factory import build_pipeline
from pcn_anpr.paddle_ocr import PaddleOCRProvider


def _synthetic_frame(seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    img = np.zeros((240, 320, 3), dtype=np.uint8)
    img[:] = (40, 40, 40)
    # Bright plate-like rectangle
    img[100:130, 80:240] = (220, 220, 220)
    noise = rng.integers(0, 20, size=img.shape, dtype=np.uint8)
    return np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def test_same_pipeline_instance_reused_across_frames() -> None:
    """Multiple frames must use one pipeline / one OCR provider instance."""
    before = PaddleOCRProvider._construct_count
    pipe = build_pipeline(ANPRSettings(provider_mode="mock"))
    ocr_id = id(pipe.ocr)
    for i in range(5):
        pipe._infer(_synthetic_frame(i), live_mode=True, debug_prefix=f"t{i}")
    assert id(pipe.ocr) == ocr_id
    # Mock path should not construct real PaddleOCR
    assert PaddleOCRProvider._construct_count == before


def test_live_mode_uses_fewer_ocr_passes_than_batch() -> None:
    from pcn_anpr.mock_providers import MockOCRProvider
    from pcn_anpr.ocr_ensemble import run_multipass_ocr

    class CountingOCR(MockOCRProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def read(self, plate_crop: object):
            self.calls += 1
            return super().read(plate_crop)

    crop = _synthetic_frame(1)[100:130, 80:240]
    batch = CountingOCR()
    live = CountingOCR()
    run_multipass_ocr(crop, batch, live_mode=False, scales=(2.0, 3.0))
    run_multipass_ocr(crop, live, live_mode=True, early_exit_on_confident=True)
    assert live.calls <= 1
    assert batch.calls > live.calls


def test_adaptive_fast_path_early_exits_and_preserves_full_fallback() -> None:
    from pcn_anpr.interfaces import OCRResult
    from pcn_anpr.mock_providers import MockOCRProvider
    from pcn_anpr.ocr_ensemble import order_variants_for_fast_path, run_multipass_ocr
    from pcn_anpr.preprocess import generate_ocr_variants

    class ScriptedOCR(MockOCRProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def read(self, plate_crop: object):
            self.calls += 1
            return OCRResult(text="MH12AB1234", confidence=0.95, raw_text="MH12AB1234")

    crop = _synthetic_frame(2)[100:130, 80:240]
    variants = generate_ocr_variants(crop, scales=(2.0, 3.0))
    ordered = order_variants_for_fast_path(variants)
    assert ordered[0].name == "gray_clahe_x2"
    assert {v.name for v in ordered} == {v.name for v in variants}

    # Full multipass (no adaptive): runs every variant that returns text
    full = ScriptedOCR()
    ens_full = run_multipass_ocr(crop, full, live_mode=False, scales=(2.0, 3.0))
    assert full.calls == len(variants)
    assert ens_full.timing.get("early_exited") is False

    # Adaptive consensus: need two agreeing confident pattern hits before stop
    fast = ScriptedOCR()
    ens_fast = run_multipass_ocr(
        crop,
        fast,
        live_mode=False,
        scales=(2.0, 3.0),
        adaptive_fast_path=True,
    )
    assert fast.calls == 2
    assert ens_fast.timing.get("early_exited") is True
    assert ens_fast.timing.get("early_exit_mode") == "consensus"
    assert ens_fast.normalized_text == "MH12AB1234"
    assert ens_fast.ocr_confident is True


def test_adaptive_fast_path_falls_back_when_not_confident() -> None:
    from pcn_anpr.interfaces import OCRResult
    from pcn_anpr.mock_providers import MockOCRProvider
    from pcn_anpr.ocr_ensemble import run_multipass_ocr
    from pcn_anpr.preprocess import generate_ocr_variants

    class EventuallyGoodOCR(MockOCRProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def read(self, plate_crop: object):
            self.calls += 1
            if self.calls < 3:
                return OCRResult(text="ZZ99YY11", confidence=0.55, raw_text="ZZ99YY11")
            return OCRResult(text="KA05MN6789", confidence=0.91, raw_text="KA05MN6789")

    crop = _synthetic_frame(3)[100:130, 80:240]
    n_variants = len(generate_ocr_variants(crop, scales=(2.0, 3.0)))
    ocr = EventuallyGoodOCR()
    ens = run_multipass_ocr(
        crop,
        ocr,
        live_mode=False,
        scales=(2.0, 3.0),
        adaptive_fast_path=True,
        min_ocr_confidence=0.3,
    )
    # Two agreeing KA05MN6789 reads required for consensus early-exit
    assert ocr.calls == 4
    assert ocr.calls < n_variants
    assert ens.normalized_text == "KA05MN6789"
    assert ens.ocr_confident is True
    assert ens.timing.get("early_exited") is True


def test_adaptive_does_not_early_exit_on_conflicting_pattern_reads() -> None:
    from pcn_anpr.interfaces import OCRResult
    from pcn_anpr.mock_providers import MockOCRProvider
    from pcn_anpr.ocr_ensemble import run_multipass_ocr

    class ConflictThenConsensus(MockOCRProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def read(self, plate_crop: object):
            self.calls += 1
            # First pass misreads D→Y; later passes agree on correct DV text.
            if self.calls == 1:
                return OCRResult(text="MH20DY2366", confidence=0.95, raw_text="MH20DY2366")
            return OCRResult(text="MH20DV2366", confidence=0.96, raw_text="MH20DV2366")

    crop = _synthetic_frame(4)[100:130, 80:240]
    ocr = ConflictThenConsensus()
    ens = run_multipass_ocr(
        crop,
        ocr,
        live_mode=False,
        scales=(2.0,),
        adaptive_fast_path=True,
        min_ocr_confidence=0.3,
    )
    # Must not stop at the single DY misread; wait until DV has consensus alone
    # (conflict clears once only DV remains as the unique pattern cluster with count>=2).
    # Sequence: DY, DV (conflict), DV (unique=DV, agreeing=2) → exit at call 3
    assert ocr.calls == 3
    assert ens.normalized_text == "MH20DV2366"
    assert ens.ocr_confident is True


@pytest.mark.benchmark
def test_live_inference_benchmark_10_frames() -> None:
    """Report first + post-warmup times on 10 frames with ONE warmed pipeline."""
    # Prefer real providers when available; fall back to mock for CI without paddle
    try:
        import cv2  # noqa: F401
        from paddleocr import PaddleOCR  # noqa: F401

        use_real = True
    except Exception:
        use_real = False

    mode = "real" if use_real else "mock"
    pipe = build_pipeline(ANPRSettings(provider_mode=mode, ocr_save_debug_crops=False))
    warm = pipe.warm_up()
    assert warm["warmup_ms"] is not None

    times: list[float] = []
    frames = [_synthetic_frame(i) for i in range(10)]
    # First measured inference after warm-up
    t0 = time.perf_counter()
    pipe._infer(frames[0], live_mode=True, debug_prefix="bench0")
    first_ms = (time.perf_counter() - t0) * 1000.0
    times.append(first_ms)

    for i in range(1, 10):
        t0 = time.perf_counter()
        pipe._infer(frames[i], live_mode=True, debug_prefix=f"bench{i}")
        times.append((time.perf_counter() - t0) * 1000.0)

    after = times[1:]
    avg_after = sum(after) / len(after)
    report = {
        "mode": mode,
        "model_init_ms": warm.get("ocr_init_ms"),
        "warmup_ms": warm.get("warmup_ms"),
        "first_infer_ms": first_ms,
        "avg_after_warmup_ms": avg_after,
        "min_ms": min(times),
        "max_ms": max(times),
        "construct_count": warm.get("construct_count"),
        "ocr_instance": warm.get("ocr_instance"),
    }
    out = Path(__file__).resolve().parents[1] / "output" / "live_bench.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\nLIVE_BENCH", json.dumps(report))

    # Same OCR instance throughout
    assert getattr(pipe.ocr, "instance_id", id(pipe.ocr)) == warm.get("ocr_instance")

    if use_real:
        # Acceptable live target after warm-up on CPU: < 2s average (ideal <1s)
        assert avg_after < 5000.0, f"post-warmup avg too slow: {avg_after:.0f}ms"
        assert max(after) < 10000.0, f"post-warmup max too slow: {max(after):.0f}ms"
    else:
        assert avg_after < 500.0
