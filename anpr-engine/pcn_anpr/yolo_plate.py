from __future__ import annotations

"""YOLO license-plate detector (EVALUATION ONLY — not Indian-validated default).

Implements ``PlateDetector`` using a **plate-specific** YOLOv8n fine-tune.
Never uses COCO ``yolov8n.pt`` (no license_plate class).

Default EVAL weights (documented in ``models/README.md``):
  Hugging Face: yasirfaizahmed/license-plate-object-detection
  File: best.pt
  Dataset: keremberke/license-plate-object-detection (CC BY 4.0)
  Model card license: Apache-2.0
  Ultralytics runtime: AGPL-3.0
"""

import logging
from pathlib import Path
from typing import Any

from pcn_anpr.interfaces import BoundingBox, PlateDetection, PlateDetector, VehicleDetection

logger = logging.getLogger(__name__)

# Documented EVAL checkpoint (not COCO yolov8n.pt).
DEFAULT_WEIGHTS_NAME = "yolov8n_plate_yasirfaizahmed_best.pt"
HF_REPO = "yasirfaizahmed/license-plate-object-detection"
HF_FILE = "best.pt"
HF_RESOLVE_URL = (
    "https://huggingface.co/yasirfaizahmed/license-plate-object-detection/resolve/main/best.pt"
)
# Hugging Face LFS SHA256 for best.pt (from model tree API lfs.oid)
EXPECTED_SHA256 = "d06657407970f80f1a12eb9f340661ecd003bbe44ff8feac3d5bc38845f11a94"


class YOLOPlateDetector(PlateDetector):
    """Ultralytics YOLO nano plate-region proposal (eval)."""

    detector_name = "yolo"

    def __init__(
        self,
        *,
        weights: str | Path | None = None,
        model_dir: str | Path | None = None,
        conf: float = 0.25,
        iou: float = 0.45,
        max_candidates: int = 12,
        min_confidence: float = 0.20,
        device: str | None = None,
        allow_download: bool = True,
    ) -> None:
        self.conf = conf
        self.iou = iou
        self.max_candidates = max_candidates
        self.min_confidence = min_confidence
        self.allow_download = allow_download
        self._device = device
        self._model = None
        self._load_error: str | None = None
        self._weights_path = self._resolve_weights(weights=weights, model_dir=model_dir)
        self.last_meta: dict[str, Any] = {
            "mode": "yolo",
            "used": "yolo",
            "weights": str(self._weights_path),
            "source": HF_RESOLVE_URL,
            "repo": HF_REPO,
            "license_note": "weights Apache-2.0 (HF card); Ultralytics AGPL-3.0; dataset CC BY 4.0",
        }

    @staticmethod
    def _resolve_weights(
        *,
        weights: str | Path | None,
        model_dir: str | Path | None,
    ) -> Path:
        if weights:
            return Path(weights)
        base = Path(model_dir) if model_dir else Path("./models")
        return base / "plate" / DEFAULT_WEIGHTS_NAME

    def _pick_device(self) -> str:
        try:
            import torch

            if not torch.cuda.is_available():
                return "cpu"
            major, minor = torch.cuda.get_device_capability(0)
            supported = getattr(torch.cuda, "get_arch_list", lambda: [])()
            sm = f"sm_{major}{minor}"
            if supported and sm not in supported and f"{major}.{minor}" not in str(supported):
                logger.warning(
                    "yolo.plate_cuda_arch_unsupported capability=%s.%s; using cpu",
                    major,
                    minor,
                )
                return "cpu"
            return "0"
        except Exception:  # noqa: BLE001
            return "cpu"

    def _ensure_weights_file(self) -> bool:
        path = self._weights_path
        if path.is_file() and path.stat().st_size > 1_000_000:
            return True
        if not self.allow_download:
            self._load_error = f"plate weights missing: {path}"
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.info(
            "yolo.plate_download start url=%s dest=%s sha256=%s",
            HF_RESOLVE_URL,
            path,
            EXPECTED_SHA256,
        )
        try:
            import hashlib
            import urllib.request

            tmp = path.with_suffix(".pt.partial")
            urllib.request.urlretrieve(HF_RESOLVE_URL, tmp)  # noqa: S310 — documented HF URL
            digest = hashlib.sha256()
            with open(tmp, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    digest.update(chunk)
            got = digest.hexdigest()
            if got != EXPECTED_SHA256:
                tmp.unlink(missing_ok=True)
                self._load_error = f"sha256 mismatch got={got} expected={EXPECTED_SHA256}"
                logger.error("yolo.plate_download_checksum_failed %s", self._load_error)
                return False
            tmp.replace(path)
            logger.info("yolo.plate_download ok path=%s sha256=%s", path, got)
            return True
        except Exception as exc:  # noqa: BLE001
            self._load_error = f"download failed: {exc}"
            logger.warning("yolo.plate_download_failed error=%s", exc)
            return False

    def _ensure_model(self) -> bool:
        if self._model is not None:
            return True
        if self._load_error is not None and self._model is None and not self._weights_path.is_file():
            # Allow retry if weights appear later
            pass
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            self._load_error = f"ultralytics not installed: {exc}"
            logger.warning("yolo.plate_unavailable reason=%s", self._load_error)
            return False
        if not self._ensure_weights_file():
            return False
        try:
            self._model = YOLO(str(self._weights_path))
            if self._device is None:
                self._device = self._pick_device()
            self._load_error = None
            logger.info(
                "yolo.plate_loaded weights=%s device=%s repo=%s",
                self._weights_path,
                self._device,
                HF_REPO,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self._load_error = str(exc)
            logger.warning("yolo.plate_load_failed error=%s", exc)
            self._model = None
            return False

    def warm_up(self) -> dict[str, Any]:
        ok = self._ensure_model()
        return {
            "plate_detector": "yolo",
            "ready": ok,
            "weights": str(self._weights_path),
            "device": self._device,
            "error": self._load_error,
        }

    @staticmethod
    def _roi(frame: Any, vehicle: VehicleDetection | None) -> tuple[Any, int, int]:
        if vehicle is None:
            return frame, 0, 0
        b = vehicle.bbox
        x1, y1 = int(b.x), int(b.y)
        x2, y2 = int(b.x + b.w), int(b.y + b.h)
        h, w = int(frame.shape[0]), int(frame.shape[1])
        # Pad slightly so bumper plates near the box edge are not clipped.
        pad_x = max(4, int((x2 - x1) * 0.06))
        pad_y = max(4, int((y2 - y1) * 0.08))
        x1, y1 = max(0, x1 - pad_x), max(0, y1 - pad_y)
        x2, y2 = min(w, x2 + pad_x), min(h, y2 + pad_y)
        if x2 - x1 < 40 or y2 - y1 < 20:
            return frame, 0, 0
        return frame[y1:y2, x1:x2], x1, y1

    def detect(self, frame: Any, vehicle: VehicleDetection | None = None) -> list[PlateDetection]:
        if frame is None or not hasattr(frame, "shape") or len(frame.shape) < 2:
            return []
        if not self._ensure_model():
            self.last_meta = {
                **self.last_meta,
                "used": "yolo_failed",
                "error": self._load_error,
                "count": 0,
            }
            return []

        roi, ox, oy = self._roi(frame, vehicle)
        rh, rw = int(roi.shape[0]), int(roi.shape[1])
        if rh < 16 or rw < 32:
            return []

        try:
            results = self._model.predict(
                source=roi,
                conf=self.conf,
                iou=self.iou,
                device=self._device,
                verbose=False,
                max_det=self.max_candidates,
            )
        except Exception as exc:  # noqa: BLE001
            if self._device != "cpu":
                self._device = "cpu"
                try:
                    results = self._model.predict(
                        source=roi,
                        conf=self.conf,
                        iou=self.iou,
                        device="cpu",
                        verbose=False,
                        max_det=self.max_candidates,
                    )
                except Exception as exc2:  # noqa: BLE001
                    logger.warning("yolo.plate_infer_failed error=%s", exc2)
                    return []
            else:
                logger.warning("yolo.plate_infer_failed error=%s", exc)
                return []

        fh, fw = int(frame.shape[0]), int(frame.shape[1])
        out: list[PlateDetection] = []
        boxes = getattr(results[0], "boxes", None) if results else None
        if boxes is None:
            self.last_meta = {**self.last_meta, "used": "yolo", "count": 0}
            return []

        for box in boxes:
            try:
                conf = float(box.conf[0].item()) if box.conf is not None else 0.0
                if conf < self.min_confidence:
                    continue
                xyxy = box.xyxy[0].tolist()
                x1 = float(xyxy[0]) + ox
                y1 = float(xyxy[1]) + oy
                x2 = float(xyxy[2]) + ox
                y2 = float(xyxy[3]) + oy
                x1 = max(0.0, min(x1, float(fw)))
                y1 = max(0.0, min(y1, float(fh)))
                x2 = max(0.0, min(x2, float(fw)))
                y2 = max(0.0, min(y2, float(fh)))
                bw, bh = x2 - x1, y2 - y1
                if bw < 16 or bh < 8:
                    continue
                aspect = bw / max(bh, 1.0)
                if aspect < 1.2 or aspect > 8.0:
                    continue
                out.append(
                    PlateDetection(
                        bbox=BoundingBox(x1, y1, bw, bh, conf),
                        confidence=conf,
                        class_name="license_plate",
                    )
                )
            except Exception:  # noqa: BLE001
                continue

        out.sort(key=lambda p: (p.confidence, p.bbox.w * p.bbox.h), reverse=True)
        out = out[: self.max_candidates]
        self.last_meta = {
            **self.last_meta,
            "used": "yolo",
            "count": len(out),
            "device": self._device,
            "vehicle_scoped": vehicle is not None,
        }
        logger.info(
            "detector=yolo_plate count=%s vehicle_scoped=%s device=%s",
            len(out),
            vehicle is not None,
            self._device,
        )
        return out
