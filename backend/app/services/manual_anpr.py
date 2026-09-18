"""Manual ANPR capture — analyze then confirm.

Reuses ``pcn_anpr.factory.build_pipeline().process_image`` (no OCR logic duplication).
Pending captures hold evidence bytes briefly until operator confirms or TTL expires.
"""

from __future__ import annotations

import base64
import json
import logging
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import app.anpr_path  # noqa: F401 — ensure pcn_anpr is importable

from app.core.config import get_settings
from app.core.exceptions import NotFoundError, ValidationAppError

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "image/bmp",
    }
)
ALLOWED_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp"})
MAX_IMAGE_BYTES = 12 * 1024 * 1024
PENDING_TTL_SECONDS = 3600

# Process-local shared pipeline (PaddleOCR init is expensive). Serialized for
# thread safety; uvicorn --reload re-imports this module and clears the cache.
_pipeline_lock = threading.RLock()
_shared_pipeline: Any | None = None
_shared_pipeline_warmed: bool = False


def reset_shared_pipeline() -> None:
    """Drop the cached pipeline (tests / clean reload)."""
    global _shared_pipeline, _shared_pipeline_warmed
    with _pipeline_lock:
        _shared_pipeline = None
        _shared_pipeline_warmed = False


def _engine_debug_stamp() -> dict[str, Any]:
    """Identify which pcn_anpr sources this process actually imported."""
    import pcn_anpr
    import pcn_anpr.pipeline as pl

    stamp: dict[str, Any] = {
        "pcn_anpr_file": getattr(pcn_anpr, "__file__", None),
        "pipeline_file": getattr(pl, "__file__", None),
        "pipeline_has_primary_roi_symbol": "detect_plates_in_primary_rois" in getattr(pl, "__dict__", {}),
    }
    try:
        import pcn_anpr.primary_roi_plate as pr

        stamp["primary_roi_plate_file"] = pr.__file__
        stamp["primary_roi_plate_mtime"] = Path(pr.__file__).stat().st_mtime
        stamp["has_detect_plates_in_primary_rois"] = hasattr(pr, "detect_plates_in_primary_rois")
    except Exception as exc:  # noqa: BLE001
        stamp["primary_roi_plate_error"] = str(exc)
        stamp["has_detect_plates_in_primary_rois"] = False
    return stamp


def build_anpr_debug(result: dict[str, Any]) -> dict[str, Any]:
    """Structured Manual ANPR diagnostics for one analyze request."""
    timing = result.get("timing") or {}
    vehicles = result.get("vehicles") or []
    primary = next((v for v in vehicles if v.get("is_primary")), None)
    if primary is None and timing.get("primary_vehicle_index") is not None:
        idx = timing.get("primary_vehicle_index")
        refs = timing.get("vehicle_refs") or []
        if isinstance(idx, int) and 0 <= idx < len(refs):
            primary = refs[idx]
    plates = result.get("plates") or []
    raw_ocr = [
        {
            "raw": p.get("raw_text"),
            "normalized": p.get("normalized_text"),
            "matches_pattern": p.get("matches_pattern"),
            "non_plate_text": p.get("non_plate_text"),
            "on_primary_vehicle": p.get("on_primary_vehicle"),
            "primary_roi_stage": p.get("primary_roi_stage"),
            "stitched_from_fragments": p.get("stitched_from_fragments"),
            "selected_variant": p.get("selected_variant"),
        }
        for p in plates[:12]
    ]
    roi_diag = timing.get("primary_roi_diagnostics") or {}
    chosen = best_plate(result)
    reject_reason = None
    if chosen is None:
        reject_reason = result.get("error") or timing.get("primary_roi_skip_reason") or "no_valid_indian_plate"
        if plates and not any(p.get("matches_pattern") for p in plates):
            reject_reason = "no_pattern_match_after_ocr"
        if timing.get("primary_roi_invoked") is False and not plates:
            reject_reason = timing.get("primary_roi_skip_reason") or "no_candidates"
    debug = {
        **_engine_debug_stamp(),
        "primary_vehicle_detected": primary is not None or timing.get("primary_vehicle_index") is not None,
        "primary_vehicle_bbox": (primary or {}).get("bbox")
        or (primary or {}).get("bbox_xyxy")
        or timing.get("selected_vehicle", {}).get("bbox"),
        "primary_vehicle_index": timing.get("primary_vehicle_index"),
        "primary_roi_invoked": bool(timing.get("primary_roi_invoked")),
        "primary_roi_skip_reason": timing.get("primary_roi_skip_reason"),
        "primary_roi_ms": timing.get("primary_roi_ms"),
        "primary_roi_proposals": timing.get("primary_roi_proposals"),
        "primary_roi_hit": timing.get("primary_roi_hit"),
        "roi_count": roi_diag.get("roi_count"),
        "roi_shapes": roi_diag.get("roi_shapes"),
        "variant_count": roi_diag.get("variant_count"),
        "variant_names": roi_diag.get("variant_names"),
        "twoline_candidates": roi_diag.get("twoline_candidates"),
        "raw_ocr": raw_ocr,
        "stitch_primary_fragments": timing.get("stitch_primary_fragments"),
        "stitch_primary_result": timing.get("stitch_primary_result"),
        "stitched_applied": timing.get("stitched_applied"),
        "selected_ocr": timing.get("selected_ocr"),
        "final_rejection_reason": reject_reason,
        "plate_detected_pipeline": bool(result.get("plate_detected")),
    }
    return debug


def get_shared_pipeline() -> Any:
    """Return a process-wide warmed ANPR pipeline (built once)."""
    global _shared_pipeline, _shared_pipeline_warmed
    with _pipeline_lock:
        if _shared_pipeline is not None and _shared_pipeline_warmed:
            return _shared_pipeline
        from pcn_anpr.factory import build_pipeline

        pipeline = build_pipeline()
        # Same warm path as LiveAnprWorker: load OCR once, then dummy pass.
        warm = pipeline.warm_up()
        _shared_pipeline = pipeline
        _shared_pipeline_warmed = True
        stamp = _engine_debug_stamp()
        logger.info(
            "manual_anpr.pipeline_ready warmup_ms=%s ocr_init_ms=%s ocr_instance=%s "
            "pipeline_file=%s primary_roi=%s",
            warm.get("warmup_ms"),
            warm.get("ocr_init_ms"),
            warm.get("ocr_instance"),
            stamp.get("pipeline_file"),
            stamp.get("has_detect_plates_in_primary_rois"),
        )
        return _shared_pipeline


@dataclass
class PendingCapture:
    capture_id: str
    organization_id: str
    site_id: str
    camera_id: str
    operator_user_id: str
    created_at: float
    snapshot_path: Path
    plate_crop_path: Path | None
    meta_path: Path


def _pending_root() -> Path:
    root = get_settings().storage_dir / "manual_pending"
    root.mkdir(parents=True, exist_ok=True)
    return root


def validate_upload(*, filename: str | None, content_type: str | None, data: bytes) -> str:
    if not data:
        raise ValidationAppError("Empty upload")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValidationAppError(f"Image too large (max {MAX_IMAGE_BYTES // (1024 * 1024)}MB)")

    ctype = (content_type or "").split(";")[0].strip().lower()
    suffix = Path(filename or "upload.jpg").suffix.lower() or ".jpg"
    if ctype and ctype not in ALLOWED_CONTENT_TYPES and ctype != "application/octet-stream":
        raise ValidationAppError(
            "Unsupported image type",
            details={"content_type": ctype, "allowed": sorted(ALLOWED_CONTENT_TYPES)},
        )
    if suffix not in ALLOWED_SUFFIXES:
        if ctype in ALLOWED_CONTENT_TYPES:
            suffix = {
                "image/jpeg": ".jpg",
                "image/jpg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
                "image/bmp": ".bmp",
            }.get(ctype, ".jpg")
        else:
            raise ValidationAppError(
                "Unsupported image format",
                details={"filename": filename, "allowed": sorted(ALLOWED_SUFFIXES)},
            )
    return suffix


def _encode_jpeg_crop(frame: Any, padded_bbox: list[int] | None) -> bytes | None:
    if frame is None or not padded_bbox or len(padded_bbox) != 4:
        return None
    try:
        import cv2
        import numpy as np

        x1, y1, x2, y2 = [int(v) for v in padded_bbox]
        h, w = frame.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return None
        crop = frame[y1:y2, x1:x2]
        if crop.size < 16:
            return None
        ok, buf = cv2.imencode(".jpg", crop)
        if not ok:
            return None
        return buf.tobytes()
    except Exception:  # noqa: BLE001
        return None


def run_anpr_on_bytes(data: bytes, *, suffix: str = ".jpg") -> dict[str, Any]:
    """Run existing ANPR pipeline on image bytes. Does not create events.

    Reuses one warmed pipeline per process (PaddleOCR models loaded once).
    Inference is serialized with a process lock for Paddle thread safety.
    """
    from pcn_anpr.timing_log import print_step, print_timing_summary, step_timer

    api_started = step_timer()
    t_write = step_timer()
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = Path(tmp.name)
    write_ms = print_step("A) Upload bytes → temp file write", t_write)
    try:
        from pcn_anpr.image_io import load_bgr

        # Hold lock across warm + process_image so concurrent requests cannot
        # race PaddleOCR / rebuild duplicate model state.
        t_pipe = step_timer()
        with _pipeline_lock:
            t_get = step_timer()
            pipeline = get_shared_pipeline()
            get_pipe_ms = print_step("B) get_shared_pipeline (warm cache)", t_get)
            result = pipeline.process_image(tmp_path)
        pipe_ms = print_step("C) pipeline.process_image TOTAL (incl. load+detect+OCR)", t_pipe)
        if result.get("error") == "invalid_image":
            raise ValidationAppError("Invalid or corrupt image")
        if result.get("error") == "image_not_found":
            raise ValidationAppError("Image could not be read")

        plate_crop_bytes: bytes | None = None
        plates = list(result.get("plates") or [])
        t_post = step_timer()
        chosen = best_plate(result)
        # Never encode a taillight / non-pattern crop as the Manual ANPR plate preview.
        if chosen is not None:
            frame = load_bgr(tmp_path)
            padded = chosen.get("padded_bbox") or chosen.get("bbox")
            plate_crop_bytes = _encode_jpeg_crop(frame, padded)
        elif plates:
            # Keep diagnostics available but do not promote invalid crops.
            result["plate_detected"] = False

        result["_plate_crop_bytes"] = plate_crop_bytes
        debug = build_anpr_debug(result)
        result["_anpr_debug"] = debug
        post_ms = print_step("D) best_plate + plate-crop encode + debug", t_post)
        total_ms = print_step("E) run_anpr_on_bytes TOTAL", api_started)
        timing = result.get("timing") if isinstance(result.get("timing"), dict) else {}
        print_timing_summary(
            "Manual ANPR run_anpr_on_bytes",
            {
                "Temp file write": write_ms,
                "Shared pipeline get/warm": get_pipe_ms,
                "process_image (engine)": pipe_ms,
                "Post (best_plate/crop)": post_ms,
                "Image load (engine)": float((timing or {}).get("step_breakdown_ms", {}).get("image_load_ms") or 0),
                "Plate detect OpenCV": float((timing or {}).get("plate_ms") or 0),
                "Preprocess/crop": float((timing or {}).get("crop_ms") or 0),
                "Primary ROI detect": float((timing or {}).get("primary_roi_ms") or 0),
                "OCR total": float((timing or {}).get("ocr_ms") or 0),
            },
            total_ms=total_ms,
        )
        logger.info(
            "manual_anpr.analyze_debug plate=%s primary_roi_invoked=%s proposals=%s reject=%s "
            "total_ms=%.1f ocr_ms=%s plate_ms=%s pipeline=%s",
            (chosen or {}).get("normalized_text") if chosen else None,
            debug.get("primary_roi_invoked"),
            debug.get("primary_roi_proposals"),
            debug.get("final_rejection_reason"),
            total_ms,
            (timing or {}).get("ocr_ms"),
            (timing or {}).get("plate_ms"),
            debug.get("pipeline_file"),
        )
        return result
    except ValidationAppError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("manual_anpr.pipeline_error %s", exc)
        raise ValidationAppError(f"ANPR processing failed: {exc}") from exc
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def best_plate(result: dict[str, Any]) -> dict[str, Any] | None:
    """Pick the best *valid* Indian plate — never finalize lights / ``IU`` / noise.

    Returns ``None`` when no candidate matches an Indian registration pattern
    (Manual ANPR must show empty / no reliable plate rather than a false crop).
    """
    plates = list(result.get("plates") or [])
    if not plates:
        return None
    try:
        from pcn_anpr.normalize import is_non_plate_text, is_plate_marker_noise, matches_indian_plate, strip_plate
        from pcn_anpr.vehicle_assoc import final_plate_rank_key
    except ImportError:  # pragma: no cover
        from app.services.plate import (  # type: ignore
            is_non_plate_text,
            is_plate_marker_noise,
            matches_indian_plate,
            strip_plate,
        )

        def final_plate_rank_key(row: dict[str, Any]) -> tuple[Any, ...]:
            return (
                1 if row.get("matches_pattern") else 0,
                1 if row.get("ocr_confident") else 0,
                1 if row.get("on_primary_vehicle") else 0,
                float(row.get("ocr_confidence") or 0.0),
            )

    def _noise(p: dict[str, Any]) -> bool:
        if p.get("marker_noise") or p.get("non_plate_text"):
            return True
        text = p.get("normalized_text") or p.get("normalized_plate") or p.get("raw_text") or ""
        return is_non_plate_text(str(text)) or is_plate_marker_noise(str(text))

    def _valid_indian(p: dict[str, Any]) -> bool:
        if _noise(p):
            return False
        if not p.get("matches_pattern"):
            return False
        text = strip_plate(
            str(p.get("normalized_text") or p.get("normalized_plate") or p.get("raw_text") or "")
        )
        return bool(text) and matches_indian_plate(text)

    usable = [p for p in plates if _valid_indian(p)]
    if not usable:
        return None
    usable.sort(key=final_plate_rank_key, reverse=True)
    return usable[0]


def save_pending(
    *,
    organization_id: str,
    site_id: str,
    camera_id: str,
    operator_user_id: str,
    snapshot_bytes: bytes,
    plate_crop_bytes: bytes | None,
    analysis: dict[str, Any],
) -> str:
    capture_id = str(uuid4())
    folder = _pending_root() / capture_id
    folder.mkdir(parents=True, exist_ok=True)
    snap = folder / "snapshot.jpg"
    snap.write_bytes(snapshot_bytes)
    crop_path: Path | None = None
    if plate_crop_bytes:
        crop_path = folder / "plate_crop.jpg"
        crop_path.write_bytes(plate_crop_bytes)
    meta = {
        "capture_id": capture_id,
        "organization_id": organization_id,
        "site_id": site_id,
        "camera_id": camera_id,
        "operator_user_id": operator_user_id,
        "created_at": time.time(),
        "analysis": {
            k: v
            for k, v in analysis.items()
            if k not in {"_plate_crop_bytes", "_anpr_debug"}
        },
    }
    (folder / "meta.json").write_text(json.dumps(meta, default=str), encoding="utf-8")
    return capture_id


def load_pending(capture_id: str, *, operator_user_id: str) -> tuple[PendingCapture, dict[str, Any], bytes, bytes | None]:
    folder = _pending_root() / capture_id
    meta_path = folder / "meta.json"
    snap_path = folder / "snapshot.jpg"
    if not meta_path.is_file() or not snap_path.is_file():
        raise NotFoundError("Capture session not found or expired")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    created = float(meta.get("created_at") or 0)
    if time.time() - created > PENDING_TTL_SECONDS:
        shutil.rmtree(folder, ignore_errors=True)
        raise NotFoundError("Capture session expired")
    if meta.get("operator_user_id") != operator_user_id:
        raise ValidationAppError("Capture session belongs to another operator")
    crop_path = folder / "plate_crop.jpg"
    plate_bytes = crop_path.read_bytes() if crop_path.is_file() else None
    pending = PendingCapture(
        capture_id=capture_id,
        organization_id=str(meta["organization_id"]),
        site_id=str(meta["site_id"]),
        camera_id=str(meta["camera_id"]),
        operator_user_id=str(meta["operator_user_id"]),
        created_at=created,
        snapshot_path=snap_path,
        plate_crop_path=crop_path if crop_path.is_file() else None,
        meta_path=meta_path,
    )
    return pending, meta, snap_path.read_bytes(), plate_bytes


def delete_pending(capture_id: str) -> None:
    folder = _pending_root() / capture_id
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)


def analysis_response(
    *,
    capture_id: str,
    organization_id: str,
    site_id: str,
    camera_id: str,
    result: dict[str, Any],
    plate_crop_bytes: bytes | None,
) -> dict[str, Any]:
    plate = best_plate(result)
    reliable = plate is not None
    plate = plate or {}
    crop_b64 = (
        base64.b64encode(plate_crop_bytes).decode("ascii")
        if plate_crop_bytes and reliable
        else None
    )
    timing = result.get("timing") or {}
    detector_mode = str(timing.get("plate_detector_mode") or "opencv")
    detector_used = str(timing.get("plate_detector_used") or detector_mode)
    ai_note = timing.get("ai_detector_note")
    err = result.get("error")
    if not reliable and not err:
        err = "no reliable plate detected"
    anpr_debug = result.get("_anpr_debug")
    if not isinstance(anpr_debug, dict):
        anpr_debug = build_anpr_debug(result)
    return {
        "capture_id": capture_id,
        "organization_id": organization_id,
        "site_id": site_id,
        "camera_id": camera_id,
        "vehicle_detected": bool(result.get("vehicle_detected")),
        "plate_detected": bool(reliable),
        "detected_plate": (plate.get("normalized_text") or plate.get("normalized_plate") or "")
        if reliable
        else "",
        "raw_ocr": (plate.get("raw_text") or "") if reliable else "",
        "normalized_plate": (plate.get("normalized_text") or plate.get("normalized_plate") or "")
        if reliable
        else "",
        "ocr_confidence": float(plate.get("ocr_confidence") or 0.0) if reliable else 0.0,
        "plate_confidence": float(plate.get("plate_confidence") or 0.0) if reliable else 0.0,
        "combined_confidence": float(plate.get("confidence") or 0.0) if reliable else 0.0,
        "matches_indian_pattern": bool(reliable and plate.get("matches_pattern")),
        "ocr_confident": bool(reliable and plate.get("ocr_confident")),
        "processing_ms": int(result.get("processing_ms") or 0),
        "bbox": list(plate.get("bbox") or []) if reliable else [],
        "plate_crop_jpeg_base64": crop_b64,
        "error": err,
        "event_created": False,
        "plate_detector_mode": detector_mode,
        "plate_detector_used": detector_used,
        "ai_detector_note": ai_note,
        "plate_candidates": timing.get("plate_candidates") or [],
        "selected_bbox": (timing.get("selected_bbox") or list(plate.get("bbox") or []))
        if reliable
        else [],
        "anpr_debug": anpr_debug,
    }
