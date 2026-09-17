from __future__ import annotations

"""CLI for Phase 6A image ANPR (no DB events).

Usage:
  python -m anpr_engine.cli --image path\\to\\frame.jpg
  python -m anpr_engine.cli --folder edge-agent\\data\\edge-frames
  python -m pcn_anpr.cli --image ...
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PCN Cloud ANPR — image inference (Phase 6A)")
    parser.add_argument("--image", type=str, help="Single image path")
    parser.add_argument("--folder", type=str, help="Folder of JPEG/PNG frames")
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory (default: ./output)")
    parser.add_argument("--provider", choices=["real", "mock"], default=None, help="Override ANPR_PROVIDER_MODE")
    parser.add_argument("--no-annotate", action="store_true", help="Skip annotated image write")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print path resolution diagnostics (also enabled when ANPR_DEBUG_MODE=true)",
    )
    args = parser.parse_args(argv)

    if not args.image and not args.folder:
        parser.error("Provide --image or --folder")

    # Late imports so --help works without heavy deps
    import os

    if args.provider:
        os.environ["ANPR_PROVIDER_MODE"] = args.provider
    from pcn_anpr.config import clear_anpr_settings_cache, get_anpr_settings

    clear_anpr_settings_cache()
    settings = get_anpr_settings()
    debug = bool(args.debug) or os.getenv("ANPR_DEBUG_MODE", "").lower() in {"1", "true", "yes", "on"}
    out_dir = Path(args.output_dir or settings.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    from pcn_anpr.factory import build_pipeline
    from pcn_anpr.image_io import resolve_image_path

    pipeline = build_pipeline(settings)

    if args.image:
        return _run_single(
            pipeline,
            resolve_image_path(args.image),
            out_dir,
            annotate=not args.no_annotate,
            debug=debug,
            supplied=args.image,
        )

    return _run_batch(pipeline, resolve_image_path(args.folder), out_dir, annotate=not args.no_annotate)


def _run_single(pipeline, image_path: Path, out_dir: Path, *, annotate: bool, debug: bool = False, supplied: str | None = None) -> int:
    from pcn_anpr.annotate import annotate_image, save_annotated
    from pcn_anpr.image_io import describe_image_path, load_bgr

    if debug:
        info = describe_image_path(supplied or image_path, image_path)
        print("PATH DEBUG")
        print("----------")
        print(f"supplied path: {info['supplied_path']}")
        print(f"resolved path: {info['resolved_path']}")
        print(f"exists: {info['exists']}")
        print(f"is_file: {info['is_file']}")
        print(f"cwd: {info['cwd']}")

    result = pipeline.process_image(image_path)
    if debug and result.get("decoded_shape") is not None:
        print(f"decoded shape: {result.get('decoded_shape')}")
    if result.get("error") == "image_not_found":
        dbg = result.get("path_debug") or describe_image_path(supplied or image_path, image_path)
        print("PATH DEBUG (image_not_found)")
        print("----------------------------")
        print(f"supplied path: {dbg.get('supplied_path')}")
        print(f"resolved path: {dbg.get('resolved_path')}")
        print(f"exists: {dbg.get('exists')}")
        print(f"is_file: {dbg.get('is_file')}")
        print(f"cwd: {dbg.get('cwd')}")
    _print_result(result)

    if annotate and result.get("error") not in {"image_not_found", "invalid_image"}:
        frame = load_bgr(image_path)
        annotated = annotate_image(frame, vehicles=result.get("vehicles"), plates=result.get("plates"))
        dest = out_dir / f"annotated_{image_path.name}"
        saved = save_annotated(annotated, dest)
        if saved:
            print(f"Annotated image: {saved}")
        else:
            print("Annotated image: (not written — OpenCV unavailable or encode failed)")

    json_path = out_dir / "results.json"
    json_path.write_text(json.dumps([_row_from_result(result)], indent=2), encoding="utf-8")
    print(f"JSON: {json_path}")
    return 0 if result.get("error") not in {"image_not_found"} else 2


def _run_batch(pipeline, folder: Path, out_dir: Path, *, annotate: bool) -> int:
    from pcn_anpr.annotate import annotate_image, save_annotated
    from pcn_anpr.image_io import load_bgr

    if not folder.is_dir():
        print(f"Folder not found: {folder}", file=sys.stderr)
        return 2

    images = sorted(
        [p for p in folder.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}]
    )
    rows: list[dict] = []
    vehicles_n = plates_n = readable_n = 0
    confidences: list[float] = []

    for path in images:
        result = pipeline.process_image(path)
        rows.append(_row_from_result(result))
        if result.get("vehicle_detected"):
            vehicles_n += 1
        if result.get("plate_detected"):
            plates_n += 1
        best = (result.get("plates") or [None])[0]
        if best and best.get("matches_pattern"):
            readable_n += 1
        if best and best.get("confidence") is not None:
            confidences.append(float(best["confidence"]))

        if annotate:
            frame = load_bgr(path)
            annotated = annotate_image(frame, vehicles=result.get("vehicles"), plates=result.get("plates"))
            save_annotated(annotated, out_dir / f"annotated_{path.name}")

    json_path = out_dir / "results.json"
    json_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    avg = sum(confidences) / len(confidences) if confidences else 0.0
    print("BATCH SUMMARY")
    print("-------------")
    print(f"Frames processed:   {len(images)}")
    print(f"Vehicles detected:  {vehicles_n}")
    print(f"Plates detected:    {plates_n}")
    print(f"Readable plates:    {readable_n}")
    print(f"Average confidence: {avg:.3f}")
    print(f"JSON: {json_path}")
    return 0


def _row_from_result(result: dict) -> dict:
    best = (result.get("plates") or [None])[0]
    ts = None
    # Try parse timestamp from filename patterns if present
    name = result.get("filename") or ""
    return {
        "filename": name,
        "timestamp": ts or datetime.now(UTC).isoformat(),
        "plate": (best or {}).get("normalized_text") if best else None,
        "raw_text": (best or {}).get("raw_text") if best else None,
        "confidence": (best or {}).get("confidence") if best else None,
        "bbox": (best or {}).get("bbox") if best else None,
        "plate_detected": bool(result.get("plate_detected")),
        "vehicle_detected": bool(result.get("vehicle_detected")),
        "matches_pattern": (best or {}).get("matches_pattern") if best else False,
        "error": result.get("error"),
        "processing_ms": result.get("processing_ms"),
    }


def _print_result(result: dict) -> None:
    plates = result.get("plates") or []
    best = plates[0] if plates else None
    print("ANPR RESULT")
    print("-----------")
    print(f"Vehicle detected: {'YES' if result.get('vehicle_detected') else 'NO'}")
    print(f"Plate detected: {'YES' if result.get('plate_detected') else 'NO'}")
    if result.get("error"):
        print(f"Error: {result['error']}")
    if best:
        print(f"Raw OCR: {best.get('raw_text') or '(empty)'}")
        norm = best.get("normalized_text")
        print(f"Normalized plate: {norm if norm is not None else '(null — not confident)'}")
        print(f"OCR confident: {'YES' if best.get('ocr_confident') else 'NO'}")
        print(f"OCR confidence: {best.get('ocr_confidence')}")
        print(f"Plate confidence: {best.get('plate_confidence')}")
        print(f"Combined confidence: {best.get('confidence')}")
        print(f"Matches Indian pattern: {best.get('matches_pattern')}")
        print(f"Selected variant: {best.get('selected_variant')}")
        print(f"Bounding box: {best.get('bbox')}")
        print(f"Padded bbox: {best.get('padded_bbox')}")
        if best.get("debug_crops"):
            print(f"Debug crops: {len(best['debug_crops'])} files")
    else:
        print("Raw OCR: (none)")
        print("Normalized plate: (none)")
        print("OCR confidence: (none)")
        print("Plate confidence: (none)")
        print("Bounding box: (none)")
    print(f"Processing ms: {result.get('processing_ms')}")


if __name__ == "__main__":
    raise SystemExit(main())
