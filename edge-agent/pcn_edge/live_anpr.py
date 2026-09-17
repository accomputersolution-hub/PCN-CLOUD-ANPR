from __future__ import annotations

"""Live ANPR worker: consume frames, confirm plates, enqueue edge events (Phase 6B).

Runs off the capture thread. ANPR failures never stop RTSP capture.
Models are loaded once at worker start and reused for every frame.
"""

import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from pcn_edge.config import EdgeSettings
from pcn_edge.frame_queue import BoundedFrameQueue
from pcn_edge.frames import Frame, _write_snapshot
from pcn_edge.sync import CloudSync
from pcn_edge.temporal import EventCoolDown, PlateObservation, TemporalPlateTracker

logger = logging.getLogger(__name__)


def _decode_bgr(jpeg: bytes) -> Any | None:
    try:
        import cv2
        import numpy as np

        arr = np.frombuffer(jpeg, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        return None


def _crop_jpeg(frame_jpeg: bytes, bbox: list[int]) -> bytes | None:
    if not bbox or len(bbox) != 4:
        return None
    img = _decode_bgr(frame_jpeg)
    if img is None:
        return None
    try:
        import cv2

        h, w = img.shape[:2]
        x1, y1, x2, y2 = [int(v) for v in bbox]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return None
        crop = img[y1:y2, x1:x2]
        ok, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        if not ok:
            return None
        return buf.tobytes()
    except Exception:
        return None


class LiveAnprWorker:
    """Background ANPR + temporal confirmation + event enqueue."""

    def __init__(
        self,
        settings: EdgeSettings,
        sync: CloudSync,
        *,
        camera_directions: dict[str, str] | None = None,
        infer_fn: Callable[[Frame], dict[str, Any]] | None = None,
        pipeline: Any | None = None,
    ) -> None:
        self.settings = settings
        self.sync = sync
        self.camera_directions = camera_directions or {}
        self.infer_fn = infer_fn
        self.queue = BoundedFrameQueue(maxsize=settings.anpr_infer_queue_size)
        self.tracker = TemporalPlateTracker(
            min_observations=settings.anpr_confirm_min_observations,
            window_seconds=settings.anpr_confirm_window_seconds,
            min_ocr_confidence=settings.anpr_min_ocr_confidence,
        )
        self.cooldown = EventCoolDown(settings.anpr_event_cooldown_seconds)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pipeline = pipeline
        self._ready = threading.Event()
        self.events_created = 0
        self.infer_failures = 0
        self.confirmations = 0
        # Metrics
        self.frames_captured = 0
        self.frames_processed = 0
        self.model_init_ms: float | None = None
        self.warmup_ms: float | None = None
        self.last_infer_ms: float | None = None
        self.infer_times_ms: list[float] = []
        self._ocr_instance_id: int | None = None
        self._ocr_construct_at_start: int | None = None

    def set_camera_direction(self, camera_id: str, direction: str) -> None:
        self.camera_directions[camera_id] = direction

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="anpr-live-worker", daemon=True)
        self._thread.start()

    def wait_ready(self, timeout: float = 300.0) -> bool:
        return self._ready.wait(timeout=timeout)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self.queue.close()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None

    def submit(self, frame: Frame) -> None:
        """Non-blocking; drops oldest if queue full."""
        self.frames_captured += 1
        self.queue.offer(frame)

    def metrics(self) -> dict[str, Any]:
        times = self.infer_times_ms
        return {
            "model_init_ms": self.model_init_ms,
            "warmup_ms": self.warmup_ms,
            "last_infer_ms": self.last_infer_ms,
            "infer_count": len(times),
            "infer_avg_ms": (sum(times) / len(times)) if times else None,
            "infer_min_ms": min(times) if times else None,
            "infer_max_ms": max(times) if times else None,
            "queue_size": self.queue.qsize(),
            "frames_captured": self.frames_captured,
            "frames_dropped": self.queue.dropped + self.queue.stale_dropped,
            "frames_dropped_capacity": self.queue.dropped,
            "frames_dropped_stale": self.queue.stale_dropped,
            "frames_processed": self.frames_processed,
            "events_created": self.events_created,
            "confirmations": self.confirmations,
            "infer_failures": self.infer_failures,
            "ocr_instance_id": self._ocr_instance_id,
            "ocr_construct_count": self._ocr_construct_at_start,
        }

    def _get_pipeline(self):
        if self._pipeline is None:
            from pcn_anpr.factory import build_pipeline
            from dataclasses import replace

            settings = None
            try:
                from pcn_anpr.config import get_anpr_settings

                base = get_anpr_settings()
                # Live path: never spam OCR debug crops unless ANPR_DEBUG_MODE
                settings = replace(
                    base,
                    ocr_save_debug_crops=bool(self.settings.anpr_debug_mode and base.ocr_save_debug_crops),
                )
            except Exception:
                settings = None
            self._pipeline = build_pipeline(settings)
        return self._pipeline

    def _warm_up(self) -> None:
        if self.infer_fn is not None:
            self.model_init_ms = 0.0
            self.warmup_ms = 0.0
            logger.info("anpr.live.warmup.skip infer_fn_override")
            return
        pipeline = self._get_pipeline()
        from pcn_anpr.paddle_ocr import PaddleOCRProvider

        self._ocr_construct_at_start = PaddleOCRProvider._construct_count
        logger.info("anpr.live.warmup.start")
        started = time.perf_counter()
        info = pipeline.warm_up()
        self.warmup_ms = (time.perf_counter() - started) * 1000.0
        self.model_init_ms = float(info.get("ocr_init_ms") or self.warmup_ms)
        self._ocr_instance_id = info.get("ocr_instance")
        logger.info(
            "anpr.live.warmup.done model_init_ms=%.0f warmup_ms=%.0f ocr_instance=%s construct_count=%s",
            self.model_init_ms or 0,
            self.warmup_ms or 0,
            self._ocr_instance_id,
            info.get("construct_count"),
        )

    def _infer(self, frame: Frame) -> dict[str, Any]:
        if self.infer_fn is not None:
            return self.infer_fn(frame)
        img = _decode_bgr(frame.data)
        if img is None:
            return {"plate_detected": False, "error": "decode_failed"}
        pipeline = self._get_pipeline()
        return pipeline._infer(
            img,
            debug_prefix=f"{frame.camera_id}_{frame.sequence}",
            live_mode=True,
        )

    def _run(self) -> None:
        logger.info(
            "anpr.live.worker.start enabled=%s confirm=%s/%ss cooldown=%ss",
            self.settings.anpr_live_enabled,
            self.settings.anpr_confirm_min_observations,
            self.settings.anpr_confirm_window_seconds,
            self.settings.anpr_event_cooldown_seconds,
        )
        try:
            self._warm_up()
        except Exception as exc:  # noqa: BLE001
            logger.exception("anpr.live.warmup.failed error=%s", exc)
            self.infer_failures += 1
        finally:
            self._ready.set()

        while not self._stop.is_set():
            frame = self.queue.get_latest(timeout=0.5)
            if frame is None:
                continue
            if not self.settings.anpr_live_enabled and not self.settings.anpr_frame_hook:
                continue
            try:
                self._handle_frame(frame)
            except Exception as exc:  # noqa: BLE001 — never kill capture via this worker
                self.infer_failures += 1
                logger.warning("anpr.live.infer_failed camera=%s error=%s", frame.camera_id, exc)

    def _handle_frame(self, frame: Frame) -> None:
        started = time.perf_counter()
        result = self._infer(frame)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.last_infer_ms = elapsed_ms
        self.infer_times_ms.append(elapsed_ms)
        if len(self.infer_times_ms) > 200:
            self.infer_times_ms = self.infer_times_ms[-100:]
        self.frames_processed += 1

        logger.info(
            "anpr.live.infer camera=%s seq=%s ms=%.0f queue=%s dropped=%s processed=%s",
            frame.camera_id,
            frame.sequence,
            elapsed_ms,
            self.queue.qsize(),
            self.queue.dropped + self.queue.stale_dropped,
            self.frames_processed,
        )

        if self.settings.debug_save_all_frames:
            _write_snapshot(
                self.settings.frame_save_dir,
                frame,
                reason="debug_all",
                max_files=self.settings.snapshot_max_files,
            )

        plates = result.get("plates") or []
        best = plates[0] if plates else None
        if not best:
            return

        if self.settings.anpr_debug_mode:
            logger.info(
                "anpr.live.debug camera=%s seq=%s raw=%s norm=%s ocr_conf=%.3f confident=%s ms=%.0f",
                frame.camera_id,
                frame.sequence,
                best.get("raw_text"),
                best.get("normalized_text"),
                float(best.get("ocr_confidence") or 0),
                best.get("ocr_confident"),
                elapsed_ms,
            )

        if self.settings.anpr_frame_hook and (best.get("plate_detected") or result.get("plate_detected")):
            if not self.settings.anpr_live_enabled:
                _write_snapshot(
                    self.settings.frame_save_dir,
                    frame,
                    reason="plate",
                    max_files=self.settings.snapshot_max_files,
                )

        if not self.settings.anpr_live_enabled:
            return

        if not best.get("ocr_confident"):
            return
        norm = best.get("normalized_text") or best.get("normalized_plate")
        if not norm:
            return
        if float(best.get("ocr_confidence") or 0) < self.settings.anpr_min_ocr_confidence:
            return
        if float(best.get("plate_confidence") or 0) < self.settings.anpr_min_plate_confidence:
            return

        vehicles = result.get("vehicles") or []
        vehicle_conf = float(vehicles[0]["confidence"]) if vehicles else 0.0
        vehicle_bbox = list(vehicles[0]["bbox"]) if vehicles else []

        obs = PlateObservation(
            camera_id=frame.camera_id,
            plate_normalized=str(norm),
            plate_raw=str(best.get("raw_text") or norm),
            ocr_confidence=float(best.get("ocr_confidence") or 0),
            plate_confidence=float(best.get("plate_confidence") or 0),
            vehicle_confidence=vehicle_conf,
            timestamp=frame.captured_at if frame.captured_at.tzinfo else frame.captured_at.replace(tzinfo=UTC),
            frame_jpeg=frame.data,
            frame_sequence=frame.sequence,
            plate_bbox=list(best.get("bbox") or []),
            padded_bbox=list(best.get("padded_bbox") or best.get("bbox") or []),
            vehicle_bbox=vehicle_bbox,
            processing_ms=int(elapsed_ms),
        )
        confirmed = self.tracker.add(obs)
        if not confirmed:
            return

        self.confirmations += 1
        if not self.cooldown.allow(confirmed.camera_id, confirmed.plate_normalized, confirmed.timestamp):
            logger.info(
                "anpr.live.cooldown_skip camera=%s plate=%s",
                confirmed.camera_id,
                confirmed.plate_normalized,
            )
            return

        direction = self.camera_directions.get(confirmed.camera_id, "ENTRY")
        if direction == "BOTH":
            direction = "ENTRY"

        plate_crop = _crop_jpeg(confirmed.best_frame_jpeg, confirmed.padded_bbox or confirmed.plate_bbox)
        vehicle_crop = _crop_jpeg(confirmed.best_frame_jpeg, confirmed.vehicle_bbox) if confirmed.vehicle_bbox else None

        snap_frame = Frame(
            camera_id=confirmed.camera_id,
            data=confirmed.best_frame_jpeg,
            captured_at=confirmed.timestamp,
            sequence=confirmed.frame_sequence,
        )
        path = _write_snapshot(
            self.settings.frame_save_dir,
            snap_frame,
            reason="confirmed",
            max_files=self.settings.snapshot_max_files,
        )
        if plate_crop and self.settings.anpr_debug_mode:
            crop_path = Path(self.settings.frame_save_dir) / f"{confirmed.camera_id}_{confirmed.frame_sequence}_platecrop.jpg"
            crop_path.parent.mkdir(parents=True, exist_ok=True)
            crop_path.write_bytes(plate_crop)

        event_id = self.sync.enqueue_detection(
            camera_id=confirmed.camera_id,
            plate=confirmed.plate_normalized,
            direction=direction,
            confidence=confirmed.ocr_confidence,
            raw_ocr_text=confirmed.plate_raw,
            plate_detection_confidence=confirmed.plate_confidence,
            vehicle_detection_confidence=confirmed.vehicle_confidence,
            processing_duration_ms=confirmed.processing_ms,
            snapshot_bytes=confirmed.best_frame_jpeg,
            plate_crop_bytes=plate_crop,
            vehicle_crop_bytes=vehicle_crop,
            timestamp=confirmed.timestamp,
        )
        self.cooldown.mark(confirmed.camera_id, confirmed.plate_normalized, confirmed.timestamp)
        self.events_created += 1
        logger.info(
            "anpr.live.event_enqueued id=%s camera=%s plate=%s direction=%s obs=%s snapshot=%s",
            event_id,
            confirmed.camera_id,
            confirmed.plate_normalized,
            direction,
            confirmed.observation_count,
            path,
        )


class CaptureFrameProcessor:
    """Capture-thread FrameProcessor: only queues frames (never runs ANPR)."""

    def __init__(
        self,
        worker: LiveAnprWorker,
        *,
        debug_save_all: bool = False,
        save_dir: str = "",
        debug_frame_saver: Any | None = None,
    ) -> None:
        self.worker = worker
        self.debug_save_all = debug_save_all
        self.save_dir = save_dir
        self.debug_frame_saver = debug_frame_saver

    def process(self, frame: Frame) -> None:
        if self.debug_save_all and self.save_dir:
            _write_snapshot(self.save_dir, frame, reason="debug_all", max_files=200)
        if self.debug_frame_saver is not None:
            self.debug_frame_saver.maybe_save(frame)
        self.worker.submit(frame)
