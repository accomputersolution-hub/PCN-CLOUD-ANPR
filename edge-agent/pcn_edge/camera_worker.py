from __future__ import annotations

"""Per-camera RTSP worker with reconnect, health reporting, and clean shutdown."""

import logging
import threading
import time
from datetime import UTC, datetime

from pcn_edge.config import EdgeSettings
from pcn_edge.frames import ANPRSnapshotProcessor, CameraHealth, FrameProcessor, FrameSource, MockRTSPFrameSource
from pcn_edge.reconnect import CameraReconnect
from pcn_edge.rtsp import CameraConfig, RTSPFrameSource, redact_url

logger = logging.getLogger(__name__)


class CameraWorker:
    """Owns one camera's capture loop. Never crashes the agent process."""

    def __init__(
        self,
        config: CameraConfig,
        settings: EdgeSettings,
        processor: FrameProcessor | None = None,
        *,
        mock: bool = False,
    ) -> None:
        self.config = config
        self.settings = settings
        self.mock = mock
        self.processor = processor or ANPRSnapshotProcessor(
            save_dir=settings.frame_save_dir,
            debug_save_all=settings.debug_save_all_frames,
            ring_size=settings.frame_ring_size,
            max_files=settings.snapshot_max_files,
            anpr_enabled=settings.anpr_frame_hook,
        )
        self.reconnect = CameraReconnect(
            EdgeSettings(
                reconnect_initial_seconds=config.reconnect_min_seconds,
                reconnect_max_seconds=config.reconnect_max_seconds,
            )
        )
        self.health = CameraHealth(camera_id=config.camera_id, status="OFFLINE")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._frames_window = 0
        self._window_start = time.monotonic()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"cam-{self.config.camera_id[:8]}",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None
        self.health.status = "OFFLINE"

    def _make_source(self) -> FrameSource:
        if self.mock or self.settings.mock_mode:
            return MockRTSPFrameSource(
                camera_id=self.config.camera_id,
                interval=self.config.frame_interval,
            )
        return RTSPFrameSource(config=self.config)

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.config.enabled:
                self.health.status = "OFFLINE"
                self.health.last_error = "Camera disabled"
                if self._stop.wait(1.0):
                    return
                continue

            source = self._make_source()
            try:
                self.health.status = "CONNECTING"
                self.health.last_error = None
                logger.info(
                    "camera.connecting id=%s url=%s",
                    self.config.camera_id,
                    redact_url(self.config.rtsp_url) if not (self.mock or self.settings.mock_mode) else "mock://",
                )
                source.open()
                self.reconnect.record_success()
                self.health.status = "ONLINE"
                self._capture_loop(source)
            except Exception as exc:  # noqa: BLE001 — never crash agent
                delay = self.reconnect.record_failure(str(exc))
                msg = str(exc)
                err = msg.lower()
                # Config/validation failures → ERROR; network/unreachable → OFFLINE
                config_fail = (
                    "rtsp url is empty" in err
                    or "must start with rtsp" in err
                    or "must include a host" in err
                    or "ffmpeg is not installed" in err
                    or "ffmpeg not found" in err
                )
                self.health.status = "ERROR" if config_fail else "OFFLINE"
                self.health.last_error = msg[:500]
                self.health.reconnect_count = self.reconnect.state.attempts
                logger.warning(
                    "camera.offline id=%s error=%s retry_in=%.1fs",
                    self.config.camera_id,
                    self.health.last_error,
                    delay,
                )
                if self._stop.wait(delay):
                    return
            finally:
                try:
                    source.close()
                except Exception:
                    pass

    def _capture_loop(self, source: FrameSource) -> None:
        while not self._stop.is_set():
            try:
                frame = source.read()
                if frame is None:
                    raise ConnectionError("Camera returned no frame")
                self.processor.process(frame)
                self.health.last_frame_at = datetime.now(UTC)
                self.health.status = "ONLINE"
                self.health.last_error = None
                self._frames_window += 1
                elapsed = time.monotonic() - self._window_start
                if elapsed >= 2.0:
                    self.health.fps = round(self._frames_window / elapsed, 2)
                    self._frames_window = 0
                    self._window_start = time.monotonic()
                if isinstance(source, RTSPFrameSource) and source.resolution:
                    self.health.resolution = source.resolution
            except Exception as exc:  # noqa: BLE001
                raise ConnectionError(str(exc)) from exc


class CameraManager:
    """Starts/stops workers for cameras the cloud marks as streaming."""

    def __init__(self, settings: EdgeSettings, processor: FrameProcessor | None = None) -> None:
        self.settings = settings
        self.processor = processor or ANPRSnapshotProcessor(
            save_dir=settings.frame_save_dir,
            debug_save_all=settings.debug_save_all_frames,
            ring_size=settings.frame_ring_size,
            max_files=settings.snapshot_max_files,
            anpr_enabled=settings.anpr_frame_hook,
        )
        self._workers: dict[str, CameraWorker] = {}
        self._lock = threading.Lock()

    def sync_configs(self, configs: list[CameraConfig]) -> None:
        wanted = {c.camera_id: c for c in configs if c.enabled}
        with self._lock:
            for cam_id in list(self._workers):
                if cam_id not in wanted:
                    self._workers[cam_id].stop()
                    del self._workers[cam_id]
            for cam_id, cfg in wanted.items():
                existing = self._workers.get(cam_id)
                if existing is None:
                    worker = CameraWorker(cfg, self.settings, self.processor, mock=self.settings.mock_mode)
                    self._workers[cam_id] = worker
                    worker.start()
                else:
                    existing.config = cfg

    def stop_all(self) -> None:
        with self._lock:
            for worker in self._workers.values():
                worker.stop()
            self._workers.clear()

    def health_snapshot(self) -> list[dict]:
        with self._lock:
            return [w.health.to_heartbeat() for w in self._workers.values()]
