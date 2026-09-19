from __future__ import annotations

"""YOLO vehicle detector (Ultralytics YOLOv8n — AGPL-3.0).

Replaces OpenCV contour proposals with COCO road-vehicle classes.
Returns [] when nothing is detected (no full-frame fallback); the pipeline
may still synthesize hosts from unhosted plates as a last resort.

Weights: official Ultralytics ``yolov8n.pt`` (see ``models/README.md``).
"""

import logging
from pathlib import Path
from typing import Any

from pcn_anpr.interfaces import BoundingBox, VehicleDetection, VehicleDetector

logger = logging.getLogger(__name__)

# COCO class ids → labels. Bicycle omitted to avoid rack/pedestrian false positives
# at ANPR gates; motorcycle covers two-wheelers of interest.
COCO_VEHICLE_CLASS_IDS: dict[int, str] = {
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}

DEFAULT_WEIGHTS_NAME = "yolov8n.pt"


class YOLOVehicleDetector(VehicleDetector):
    """Ultralytics YOLO nano vehicle detector.

    Attributes used by the pipeline:
    - ``detector_name`` — logging / timing
    - ``prefer_detector_hosts`` — suppress unnecessary plate→vehicle synthesis
    - ``full_frame_fallback`` — False (empty list when no vehicles)
    """

    detector_name = "yolo"
    prefer_detector_hosts = True
    full_frame_fallback = False

    def __init__(
        self,
        *,
        weights: str | Path | None = None,
        model_dir: str | Path | None = None,
        conf: float = 0.35,
        iou: float = 0.45,
        max_detections: int = 8,
        device: str | None = None,
    ) -> None:
        self.conf = conf
        self.iou = iou
        self.max_detections = max_detections
        self._device = device
        self._model = None
        self._weights_path = self._resolve_weights(weights=weights, model_dir=model_dir)
        self._load_error: str | None = None

    @staticmethod
    def _resolve_weights(
        *,
        weights: str | Path | None,
        model_dir: str | Path | None,
    ) -> Path:
        if weights:
            return Path(weights)
        base = Path(model_dir) if model_dir else Path("./models")
        return base / "vehicle" / DEFAULT_WEIGHTS_NAME

    def _ensure_model(self) -> bool:
        if self._model is not None:
            return True
        if self._load_error is not None:
            return False
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            self._load_error = f"ultralytics not installed: {exc}"
            logger.warning("yolo.vehicle_unavailable reason=%s", self._load_error)
            return False

        path = self._weights_path
        # Prefer local file; otherwise pass the official name so Ultralytics can
        # download yolov8n.pt from its documented GitHub release assets once.
        load_arg: str | Path = path if path.is_file() else DEFAULT_WEIGHTS_NAME
        try:
            self._model = YOLO(str(load_arg))
            if self._device is None:
                self._device = self._pick_device()
            # Persist under model_dir when Ultralytics downloaded into CWD.
            if not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                downloaded = Path(DEFAULT_WEIGHTS_NAME)
                if downloaded.is_file():
                    try:
                        downloaded.replace(path)
                    except OSError:
                        pass
            logger.info(
                "yolo.vehicle_loaded weights=%s device=%s",
                path if path.is_file() else load_arg,
                self._device,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self._load_error = str(exc)
            logger.warning("yolo.vehicle_load_failed error=%s", exc)
            self._model = None
            return False

    @staticmethod
    def _pick_device() -> str:
        """Prefer CUDA when usable; RTX 50-series (sm_120) needs newer torch — use CPU then."""
        try:
            import torch

            if not torch.cuda.is_available():
                return "cpu"
            # Blackwell / sm_120 may report available but fail kernels on older torch builds.
            major, minor = torch.cuda.get_device_capability(0)
            supported = getattr(torch.cuda, "get_arch_list", lambda: [])()
            sm = f"sm_{major}{minor}"
            if supported and sm not in supported and f"{major}.{minor}" not in str(supported):
                logger.warning(
                    "yolo.cuda_arch_unsupported capability=%s.%s arch_list=%s; using cpu",
                    major,
                    minor,
                    supported,
                )
                return "cpu"
            return "0"
        except Exception:  # noqa: BLE001
            return "cpu"

    def detect(self, frame: Any) -> list[VehicleDetection]:
        if frame is None:
            return []
        if not hasattr(frame, "shape") or len(getattr(frame, "shape", ())) < 2:
            return []
        if not self._ensure_model():
            return []

        h, w = int(frame.shape[0]), int(frame.shape[1])
        try:
            results = self._model.predict(
                source=frame,
                conf=self.conf,
                iou=self.iou,
                classes=list(COCO_VEHICLE_CLASS_IDS.keys()),
                device=self._device,
                verbose=False,
                max_det=self.max_detections,
            )
        except Exception as exc:  # noqa: BLE001
            if self._device != "cpu":
                logger.warning("yolo.vehicle_gpu_failed error=%s; retrying on cpu", exc)
                self._device = "cpu"
                try:
                    results = self._model.predict(
                        source=frame,
                        conf=self.conf,
                        iou=self.iou,
                        classes=list(COCO_VEHICLE_CLASS_IDS.keys()),
                        device="cpu",
                        verbose=False,
                        max_det=self.max_detections,
                    )
                except Exception as exc2:  # noqa: BLE001
                    logger.warning("yolo.vehicle_infer_failed error=%s", exc2)
                    return []
            else:
                logger.warning("yolo.vehicle_infer_failed error=%s", exc)
                return []

        detections: list[VehicleDetection] = []
        if not results:
            logger.info("detector=yolo vehicle_count=0")
            return []

        boxes = getattr(results[0], "boxes", None)
        if boxes is None or len(boxes) == 0:
            logger.info("detector=yolo vehicle_count=0")
            return []

        for box in boxes:
            try:
                cls_id = int(box.cls[0].item()) if box.cls is not None else -1
                label = COCO_VEHICLE_CLASS_IDS.get(cls_id)
                if label is None:
                    continue
                conf = float(box.conf[0].item()) if box.conf is not None else 0.0
                xyxy = box.xyxy[0].tolist()
                x1, y1, x2, y2 = (float(xyxy[0]), float(xyxy[1]), float(xyxy[2]), float(xyxy[3]))
                x1 = max(0.0, min(x1, float(w)))
                y1 = max(0.0, min(y1, float(h)))
                x2 = max(0.0, min(x2, float(w)))
                y2 = max(0.0, min(y2, float(h)))
                bw, bh = x2 - x1, y2 - y1
                if bw < 16 or bh < 16:
                    continue
                detections.append(
                    VehicleDetection(
                        bbox=BoundingBox(x1, y1, bw, bh, conf),
                        label=label,
                        confidence=conf,
                    )
                )
            except Exception:  # noqa: BLE001
                continue

        detections.sort(key=lambda v: v.confidence, reverse=True)
        detections = _nms_vehicles(detections, iou_thresh=0.45)
        detections = detections[: self.max_detections]

        labels = [str(v.label or "") for v in detections]
        moto_count = sum(1 for lab in labels if lab == "motorcycle")
        logger.info(
            "detector=yolo vehicle_count=%s yolo_motorcycle_count=%s vehicle_classes=%s",
            len(detections),
            moto_count,
            labels,
        )
        print(
            f"[ANPR YOLO] vehicle_count={len(detections)} "
            f"yolo_motorcycle_count={moto_count} vehicle_classes={labels}",
            flush=True,
        )
        if moto_count == 0:
            msg = (
                f"yolo_motorcycle_absent: no motorcycle class in detections; "
                f"classes={labels}"
            )
            logger.info(msg)
            print(f"[ANPR YOLO] {msg}", flush=True)
        for i, v in enumerate(detections):
            b = v.bbox
            logger.info(
                "vehicle[%s] class=%s conf=%.3f bbox=[%.0f,%.0f,%.0f,%.0f]",
                i,
                v.label,
                v.confidence,
                b.x,
                b.y,
                b.x + b.w,
                b.y + b.h,
            )
        return detections


def _nms_vehicles(
    detections: list[VehicleDetection],
    *,
    iou_thresh: float = 0.45,
) -> list[VehicleDetection]:
    """Class-agnostic NMS so car/truck duplicates on the same region collapse."""
    kept: list[VehicleDetection] = []
    for cand in detections:
        cx1, cy1 = cand.bbox.x, cand.bbox.y
        cx2, cy2 = cand.bbox.x + cand.bbox.w, cand.bbox.y + cand.bbox.h
        carea = max(0.0, cx2 - cx1) * max(0.0, cy2 - cy1)
        drop = False
        for k in kept:
            kx1, ky1 = k.bbox.x, k.bbox.y
            kx2, ky2 = k.bbox.x + k.bbox.w, k.bbox.y + k.bbox.h
            ix1, iy1 = max(cx1, kx1), max(cy1, ky1)
            ix2, iy2 = min(cx2, kx2), min(cy2, ky2)
            inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
            union = carea + max(0.0, kx2 - kx1) * max(0.0, ky2 - ky1) - inter
            if union > 0 and inter / union >= iou_thresh:
                drop = True
                break
        if not drop:
            kept.append(cand)
    return kept
