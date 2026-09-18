"""Profile / smoke ANPR process_image with adaptive fast-path timing."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / "anpr-engine"))
sys.path.insert(0, str(ROOT))

from pcn_anpr.factory import build_pipeline


def smoke(path: Path, *, label: str, pipe) -> dict:
    t0 = time.perf_counter()
    result = pipe.process_image(path)
    wall_ms = (time.perf_counter() - t0) * 1000.0
    plates = result.get("plates") or []
    best = plates[0] if plates else {}
    ocr_timing = best.get("ocr_timing") or {}
    out = {
        "label": label,
        "file": path.name,
        "wall_ms": round(wall_ms, 1),
        "processing_ms": result.get("processing_ms"),
        "timing": result.get("timing"),
        "normalized": best.get("normalized_text") or best.get("normalized_plate"),
        "raw": best.get("raw_text"),
        "ocr_confidence": best.get("ocr_confidence"),
        "selected_variant": best.get("selected_variant"),
        "ocr_confident": best.get("ocr_confident"),
        "passes_run": len(best.get("ocr_passes") or []),
        "ocr_timing": {
            "preprocess_ms": ocr_timing.get("preprocess_ms"),
            "ocr_total_ms": ocr_timing.get("ocr_total_ms"),
            "variants_total": ocr_timing.get("variants_total"),
            "variants_run": ocr_timing.get("variants_run"),
            "early_exited": ocr_timing.get("early_exited"),
            "pass_timings": ocr_timing.get("pass_timings"),
        },
        "debug_crops": len(best.get("debug_crops") or []),
    }
    print(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    images = [
        Path(r"C:\Users\mdsal\Downloads\car.jpg"),
        Path(r"C:\Users\mdsal\Downloads\car_low_320x240.jpg"),
        Path(r"C:\Users\mdsal\Downloads\car_low_160x120.jpg"),
    ]
    pipe = build_pipeline()
    print("=== WARMUP ===")
    print(json.dumps(pipe.warm_up(), indent=2))
    for img in images:
        if not img.is_file():
            print(f"SKIP missing {img}")
            continue
        print(f"\n=== SMOKE {img.name} (warm) ===")
        smoke(img, label="warm", pipe=pipe)
