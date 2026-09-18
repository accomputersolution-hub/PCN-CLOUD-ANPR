#!/usr/bin/env python3
"""Evaluate OpenCV vs AI plate detectors on local testdata images.

Does not download models. When ANPR_AI_PLATE_MODEL_DIR is unset/invalid, AI fields
are null and a note is written into the report.

Usage (from anpr-engine/):
  python scripts/eval_plate_detectors.py
  python scripts/eval_plate_detectors.py --out output/plate_det_eval.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pcn_anpr.ai_plate import AILicensePlateDetector
from pcn_anpr.config import ANPRSettings, clear_anpr_settings_cache
from pcn_anpr.factory import build_pipeline
from pcn_anpr.image_io import load_bgr
from pcn_anpr.opencv_plate import OpenCVPlateDetector

TESTDATA = ROOT / "testdata"

# image stem / filename → expected plate (when present)
CASES: list[tuple[str, str]] = [
    ("MH20DV2366_car.jpg", "MH20DV2366"),
    ("MH20DV2366_320x240.jpg", "MH20DV2366"),
    ("BH_22BH6517TA.jpg", "22BH6517TA"),
    ("BH_22BH6517TA_scene.jpg", "22BH6517TA"),
    ("TN51Y6552_alamy.jpg", "TN51Y6552"),
    ("GA09D3030.jpg", "GA09D3030"),
    ("GA09D3030_car.jpg", "GA09D3030"),
]


def _best_bbox_conf(dets: list[Any]) -> tuple[list[float] | None, float | None]:
    if not dets:
        return None, None
    p = dets[0]
    b = p.bbox
    return [float(b.x), float(b.y), float(b.x + b.w), float(b.y + b.h)], float(p.confidence)


def _timed_detect(detector: Any, frame: Any) -> tuple[list[Any], float]:
    t0 = time.perf_counter()
    dets = list(detector.detect(frame, None) or [])
    ms = (time.perf_counter() - t0) * 1000.0
    return dets, ms


def evaluate_one(
    path: Path,
    expected: str,
    *,
    opencv: OpenCVPlateDetector,
    ai: AILicensePlateDetector,
    pipeline: Any,
    ai_note: str | None,
) -> dict[str, Any]:
    frame = load_bgr(path)
    total_t0 = time.perf_counter()
    row: dict[str, Any] = {
        "image": path.name,
        "expected_plate": expected,
        "opencv_bbox": None,
        "opencv_conf": None,
        "opencv_time_ms": None,
        "ai_bbox": None,
        "ai_conf": None,
        "ai_time_ms": None,
        "final_plate": None,
        "ocr_conf": None,
        "total_time_ms": None,
        "note": ai_note,
        "path": str(path),
    }
    if frame is None:
        row["note"] = (ai_note or "") + "; invalid_image"
        row["total_time_ms"] = round((time.perf_counter() - total_t0) * 1000.0, 2)
        return row

    cv_dets, cv_ms = _timed_detect(opencv, frame)
    cv_bbox, cv_conf = _best_bbox_conf(cv_dets)
    row["opencv_bbox"] = cv_bbox
    row["opencv_conf"] = cv_conf
    row["opencv_time_ms"] = round(cv_ms, 2)

    ai_dets, ai_ms = _timed_detect(ai, frame)
    if ai.init_error and not ai.path_configured:
        row["ai_bbox"] = None
        row["ai_conf"] = None
        row["ai_time_ms"] = None
        row["note"] = ai_note or ai.init_error
    elif not ai_dets:
        row["ai_bbox"] = None
        row["ai_conf"] = None
        row["ai_time_ms"] = round(ai_ms, 2)
        row["note"] = ai_note or (
            "AI model path missing/inactive — no AI detections "
            "(OpenCV+OCR path still evaluated)"
        )
    else:
        ai_bbox, ai_conf = _best_bbox_conf(ai_dets)
        row["ai_bbox"] = ai_bbox
        row["ai_conf"] = ai_conf
        row["ai_time_ms"] = round(ai_ms, 2)

    # Full pipeline (default OpenCV detector via settings) for final plate / OCR
    result = pipeline.process_image(path)
    plates = list(result.get("plates") or [])
    if plates:
        best = plates[0]
        row["final_plate"] = best.get("normalized_text") or best.get("raw_text")
        row["ocr_conf"] = best.get("ocr_confidence")
    row["total_time_ms"] = round((time.perf_counter() - total_t0) * 1000.0, 2)
    row["matches_expected"] = (
        str(row["final_plate"] or "").replace(" ", "").upper() == expected.upper()
        if row["final_plate"]
        else False
    )
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare OpenCV vs AI plate detectors")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "output" / "plate_det_eval.json",
        help="JSON report path (CSV written beside it)",
    )
    parser.add_argument("--testdata", type=Path, default=TESTDATA)
    args = parser.parse_args(argv)

    clear_anpr_settings_cache()
    settings = ANPRSettings(provider_mode="real", plate_detector="opencv")
    opencv = OpenCVPlateDetector(min_confidence=settings.min_plate_confidence)
    ai = AILicensePlateDetector(
        model_dir=settings.ai_plate_model_dir
        or __import__("os").getenv("ANPR_AI_PLATE_MODEL_DIR", ""),
        min_confidence=settings.min_plate_confidence,
    )
    ai.initialize()
    ai_note = None
    if ai.init_error and not ai.path_configured:
        ai_note = "AI model absent/invalid — ai_* fields empty"
    elif ai.path_configured and not ai.available:
        ai_note = (
            "AI model path present but inference disabled in PoC "
            "(CCPD/PP-Vehicle not auto-run) — ai_* detections empty"
        )

    pipe = build_pipeline(settings)
    try:
        pipe.warm_up()
    except Exception:
        pass

    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for filename, expected in CASES:
        path = args.testdata / filename
        if not path.is_file():
            missing.append(filename)
            continue
        rows.append(
            evaluate_one(
                path,
                expected,
                opencv=opencv,
                ai=ai,
                pipeline=pipe,
                ai_note=ai_note,
            )
        )

    report = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "ai_note": ai_note,
        "ai_init_error": ai.init_error,
        "ai_path_configured": ai.path_configured,
        "missing_images": missing,
        "rows": rows,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    csv_path = args.out.with_suffix(".csv")
    fieldnames = [
        "image",
        "opencv_bbox",
        "opencv_conf",
        "opencv_time_ms",
        "ai_bbox",
        "ai_conf",
        "ai_time_ms",
        "final_plate",
        "ocr_conf",
        "total_time_ms",
        "expected_plate",
        "matches_expected",
        "note",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            flat = dict(r)
            flat["opencv_bbox"] = json.dumps(r.get("opencv_bbox")) if r.get("opencv_bbox") else ""
            flat["ai_bbox"] = json.dumps(r.get("ai_bbox")) if r.get("ai_bbox") else ""
            writer.writerow(flat)

    print(f"Wrote {args.out}")
    print(f"Wrote {csv_path}")
    print(f"Images evaluated: {len(rows)}; missing fixtures: {missing}")
    if ai_note:
        print(f"Note: {ai_note}")
    for r in rows:
        print(
            f"  {r['image']}: plate={r.get('final_plate')!r} "
            f"opencv_ms={r.get('opencv_time_ms')} ai_ms={r.get('ai_time_ms')} "
            f"ok={r.get('matches_expected')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
