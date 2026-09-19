"""ANPR camera calibration — run hybrid pipeline + score; persist latest/previous only."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import cv2
import numpy as np

from app.core.exceptions import ValidationAppError
from app.services import manual_anpr as anpr_svc

logger = logging.getLogger(__name__)


def _roi_dict(camera: Any) -> dict[str, Any] | None:
    raw = getattr(camera, "anpr_roi", None)
    if raw is None:
        return None
    if hasattr(raw, "model_dump"):
        return raw.model_dump()
    if isinstance(raw, dict):
        return raw
    return None


def _decode_bgr(data: bytes) -> Any | None:
    arr = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _stored_blob(existing: Any) -> dict[str, Any] | None:
    if existing is None:
        return None
    if hasattr(existing, "model_dump"):
        return existing.model_dump()
    if isinstance(existing, dict):
        return existing
    return None


def run_calibration_on_bytes(
    data: bytes,
    *,
    camera: Any,
    camera_id: str,
    suffix: str = ".jpg",
) -> dict[str, Any]:
    """Run ANPR on uploaded frame, score calibration, return report + stored metadata.

    Does not create ANPR events. Does not retain JPEG after the response.
    """
    if not data:
        raise ValidationAppError("Empty image upload")

    anpr_roi = _roi_dict(camera)
    result = anpr_svc.run_anpr_on_bytes(
        data,
        suffix=suffix,
        anpr_roi=anpr_roi,
        camera_id=camera_id,
    )

    from pcn_anpr.calibration import build_calibration_report, calibration_summary_for_storage

    frame = _decode_bgr(data)
    report = build_calibration_report(
        result,
        frame_bgr=frame,
        anpr_roi=anpr_roi,
        camera_id=camera_id,
    )

    summary = calibration_summary_for_storage(report)
    previous_blob = _stored_blob(getattr(camera, "anpr_calibration", None))
    previous_summary = (previous_blob or {}).get("latest") if previous_blob else None

    now = datetime.now(timezone.utc).isoformat()
    stored = {
        "updated_at": now,
        "latest": summary,
        "previous": previous_summary,
    }

    metrics = report.get("metrics") or {}
    logger.info(
        "camera_calibration camera_id=%s roi_enabled=%s vehicle_count=%s plate_count=%s "
        "plate_width=%s plate_height=%s vehicle_conf=%s plate_conf=%s ocr_conf=%s "
        "brightness=%s sharpness=%s final_status=%s processing_ms=%s",
        camera_id,
        metrics.get("roi_enabled"),
        metrics.get("vehicle_count"),
        metrics.get("plate_count"),
        metrics.get("plate_width_px"),
        metrics.get("plate_height_px"),
        metrics.get("vehicle_confidence"),
        metrics.get("plate_confidence"),
        metrics.get("ocr_confidence"),
        metrics.get("brightness"),
        metrics.get("sharpness"),
        report.get("status"),
        metrics.get("processing_ms"),
    )

    return {
        "report": report,
        "anpr_calibration": stored,
        "previous": previous_summary,
    }
