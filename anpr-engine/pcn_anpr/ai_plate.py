from __future__ import annotations

"""AI license-plate detector (PoC placeholder — Apache 2.0 Paddle stack only).

Does NOT download weights. Until ``ANPR_AI_PLATE_MODEL_DIR`` points at a verified
local inference directory, ``detect()`` returns [] and logs once.

Even with a local path present, this PoC does **not** auto-run CCPD-tuned
PP-Vehicle weights (Chinese plates; commercial redistribution caution). See
``anpr-engine/models/README.md``. Inference returns [] until an approved
backend is hooked in ``_try_load_predictor`` / ``_run_inference``.
"""

import logging
import time
from pathlib import Path
from typing import Any

from pcn_anpr.interfaces import BoundingBox, PlateDetection, PlateDetector, VehicleDetection

logger = logging.getLogger(__name__)

_PADDLE_MODEL_FILES = ("inference.pdmodel", "inference.pdiparams")
_ONNX_CANDIDATES = ("model.onnx", "plate_det.onnx", "inference.onnx")
_PATH_ONLY = object()  # sentinel: dir validated, inference not enabled


def _looks_like_model_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    if all((path / name).is_file() for name in _PADDLE_MODEL_FILES):
        return True
    if any((path / name).is_file() for name in _ONNX_CANDIDATES):
        return True
    children = [p for p in path.iterdir() if p.is_dir()]
    if len(children) == 1:
        return _looks_like_model_dir(children[0])
    return False


def _resolve_model_dir(raw: str) -> Path | None:
    if not raw or not str(raw).strip():
        return None
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        return None
    if path.is_file():
        return path.parent if _looks_like_model_dir(path.parent) else None
    if _looks_like_model_dir(path):
        children = [p for p in path.iterdir() if p.is_dir() and _looks_like_model_dir(p)]
        if len(children) == 1 and not all((path / n).is_file() for n in _PADDLE_MODEL_FILES):
            return children[0]
        return path
    return None


def _xyxy_valid(x1: float, y1: float, x2: float, y2: float, fw: int, fh: int) -> bool:
    if x2 <= x1 or y2 <= y1:
        return False
    if x1 >= fw or y1 >= fh or x2 <= 0 or y2 <= 0:
        return False
    if (x2 - x1) < 16 or (y2 - y1) < 8:
        return False
    return True


class AILicensePlateDetector(PlateDetector):
    """Neural plate-region proposal. Load once per process; empty until usable."""

    _construct_count: int = 0
    _ensure_calls: int = 0

    def __init__(
        self,
        *,
        model_dir: str = "",
        min_confidence: float = 0.25,
        max_candidates: int = 8,
        eager: bool = False,
    ) -> None:
        self.model_dir = (model_dir or "").strip()
        self.min_confidence = min_confidence
        self.max_candidates = max_candidates
        self._predictor: Any | None = None
        self._resolved_dir: Path | None = None
        self._init_error: str | None = None
        self._init_ms: float | None = None
        self._missing_logged = False
        self._inference_disabled_logged = False
        self._path_configured = False
        self.instance_id = id(self)
        AILicensePlateDetector._construct_count += 1
        if eager:
            self.initialize()

    def initialize(self) -> bool:
        """Validate model path once (and load predictor if an approved backend exists)."""
        self._ensure()
        return self._path_configured and self._init_error is None

    def warm_up(self) -> dict[str, Any]:
        """Load/validate model once and run a dummy detect."""
        import numpy as np

        started = time.perf_counter()
        ok = self.initialize()
        dummy = np.zeros((96, 192, 3), dtype=np.uint8)
        dummy[30:66, 40:152] = 240
        dets = self.detect(dummy, None)
        return {
            "ai_plate_path_configured": self._path_configured,
            "ai_plate_inference_ready": self._predictor is not None and self._predictor is not _PATH_ONLY,
            "ai_plate_init_ms": self._init_ms,
            "ai_plate_init_error": self._init_error,
            "warmup_ms": (time.perf_counter() - started) * 1000.0,
            "dummy_detections": len(dets),
            "construct_count": AILicensePlateDetector._construct_count,
            "instance": self.instance_id,
            "ok": ok,
        }

    def _ensure(self) -> Any | None:
        AILicensePlateDetector._ensure_calls += 1
        if self._predictor is not None:
            return self._predictor if self._predictor is not _PATH_ONLY else None
        if self._init_error and not self._path_configured:
            return None

        started = time.perf_counter()
        resolved = _resolve_model_dir(self.model_dir)
        if resolved is None:
            self._init_error = (
                "AI plate model not configured or invalid. "
                "Set ANPR_AI_PLATE_MODEL_DIR to a local inference directory "
                "(see anpr-engine/models/README.md). AI mode returns no detections."
            )
            self._log_missing_once()
            return None

        self._resolved_dir = resolved
        self._path_configured = True
        try:
            predictor = self._try_load_predictor(resolved)
        except Exception as exc:  # noqa: BLE001
            self._init_error = f"AI plate model load failed: {exc}"
            logger.warning("plate.ai.init_failed %s", self._init_error)
            return None

        self._init_ms = (time.perf_counter() - started) * 1000.0
        if predictor is None:
            # Path OK; inference intentionally inactive for this PoC.
            self._predictor = _PATH_ONLY
            self._init_error = None
            if not self._inference_disabled_logged:
                self._inference_disabled_logged = True
                logger.info(
                    "plate.ai.path_ok_inference_disabled dir=%s "
                    "(CCPD/PP-Vehicle weights not auto-run; see models/README.md)",
                    resolved,
                )
            return None

        self._predictor = predictor
        self._init_error = None
        logger.info(
            "plate.ai.ready dir=%s init_ms=%.0f construct_count=%s instance=%s",
            resolved,
            self._init_ms,
            AILicensePlateDetector._construct_count,
            self.instance_id,
        )
        return self._predictor

    def _try_load_predictor(self, model_dir: Path) -> Any | None:
        """Hook for an approved inference backend. Returns None in this PoC."""
        _ = model_dir
        return None

    def _log_missing_once(self) -> None:
        if self._missing_logged:
            return
        self._missing_logged = True
        logger.warning("plate.ai.unavailable %s", self._init_error)

    @property
    def available(self) -> bool:
        """True only when a real inference predictor is loaded."""
        pred = self._ensure()
        return pred is not None

    @property
    def path_configured(self) -> bool:
        self._ensure()
        return self._path_configured

    @property
    def init_error(self) -> str | None:
        self._ensure()
        return self._init_error

    @property
    def init_ms(self) -> float | None:
        return self._init_ms

    def detect(self, frame: Any, vehicle: VehicleDetection | None = None) -> list[PlateDetection]:
        started = time.perf_counter()
        if frame is None or not hasattr(frame, "shape") or len(frame.shape) < 2:
            return []

        # Ensure init side-effects (log missing once / path_ok) happen every process.
        predictor = self._ensure()
        if predictor is None:
            return []

        try:
            raw = self._run_inference(predictor, frame, vehicle)
        except Exception as exc:  # noqa: BLE001
            logger.warning("plate.ai.infer_failed %s", exc)
            return []

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        fh, fw = int(frame.shape[0]), int(frame.shape[1])
        out: list[PlateDetection] = []
        for item in raw or []:
            try:
                bbox_xyxy, conf = self._parse_raw_det(item)
            except (TypeError, ValueError, KeyError):
                continue
            if conf < self.min_confidence:
                continue
            x1, y1, x2, y2 = bbox_xyxy
            x1 = max(0.0, min(float(fw), float(x1)))
            y1 = max(0.0, min(float(fh), float(y1)))
            x2 = max(0.0, min(float(fw), float(x2)))
            y2 = max(0.0, min(float(fh), float(y2)))
            if not _xyxy_valid(x1, y1, x2, y2, fw, fh):
                continue
            out.append(
                PlateDetection(
                    bbox=BoundingBox(x1, y1, x2 - x1, y2 - y1, float(conf)),
                    confidence=float(conf),
                    class_name="license_plate",
                    timing_ms=elapsed_ms,
                )
            )
        out.sort(key=lambda p: (p.confidence, p.bbox.w * p.bbox.h), reverse=True)
        return out[: self.max_candidates]

    def _run_inference(
        self,
        predictor: Any,
        frame: Any,
        vehicle: VehicleDetection | None,
    ) -> list[Any]:
        _ = predictor, frame, vehicle
        return []

    @staticmethod
    def _parse_raw_det(item: Any) -> tuple[tuple[float, float, float, float], float]:
        if isinstance(item, PlateDetection):
            b = item.bbox
            return (b.x, b.y, b.x + b.w, b.y + b.h), float(item.confidence)
        if isinstance(item, dict):
            conf = float(item.get("confidence") or item.get("score") or 0.0)
            box = item.get("bbox") or item.get("box")
            if box is None:
                raise ValueError("missing bbox")
            coords = list(box)
            if len(coords) != 4:
                raise ValueError("bad bbox")
            a, b, c, d = (float(x) for x in coords)
            if c > a and d > b and (c - a) > 1.0:
                return (a, b, c, d), conf
            return (a, b, a + c, b + d), conf
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            box, conf = item[0], float(item[1])
            a, b, c, d = (float(x) for x in box[:4])
            if c > a and d > b:
                return (a, b, c, d), conf
            return (a, b, a + c, b + d), conf
        raise ValueError("unsupported detection shape")

    def to_dict_detections(self, plates: list[PlateDetection]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for p in plates:
            rows.append(
                {
                    "bbox": [p.bbox.x, p.bbox.y, p.bbox.x + p.bbox.w, p.bbox.y + p.bbox.h],
                    "confidence": float(p.confidence),
                    "class_name": p.class_name or "license_plate",
                    "timing_ms": p.timing_ms,
                }
            )
        return rows
