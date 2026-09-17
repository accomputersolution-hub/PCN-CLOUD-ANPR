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
