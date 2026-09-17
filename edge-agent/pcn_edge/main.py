from __future__ import annotations

import logging
import signal
import time

from pcn_anpr.pipeline import ANPRPipeline
from pcn_edge.camera_worker import CameraManager
from pcn_edge.config import EdgeSettings, get_edge_settings
from pcn_edge.frames import ANPRSnapshotProcessor
from pcn_edge.health import sample_health
from pcn_edge.live_anpr import CaptureFrameProcessor, LiveAnprWorker
from pcn_edge.debug_frames import DebugSampledFrameSaver
from pcn_edge.queue import EventQueue
from pcn_edge.rtsp import CameraConfig, find_ffmpeg
from pcn_edge.sync import CloudSync

logger = logging.getLogger(__name__)


def print_startup_diagnostics(settings: EdgeSettings) -> None:
    """Clear, human-readable banner printed once at agent start."""
    ffmpeg = find_ffmpeg()
    agent_id = settings.edge_agent_id or "(not set)"
    save_mode = "ALL frames (debug)" if settings.debug_save_all_frames else "detection / confirmed snapshots only"
    lines = [
        "",
        "======== PCN Cloud Edge Agent ========",
        f"  FFmpeg:        {'FOUND - ' + ffmpeg if ffmpeg else 'NOT FOUND (install and add to PATH)'}",
        f"  Edge Agent ID: {agent_id}",
        f"  Backend URL:   {settings.api_base_url}",
        f"  RTSP enabled:  {settings.rtsp_enabled}",
        f"  Mock mode:     {settings.mock_mode}",
        f"  ANPR live:     {settings.anpr_live_enabled}",
        f"  ANPR debug:    {settings.anpr_debug_mode}",
        f"  ANPR hook:     {settings.anpr_frame_hook}",
        f"  Sample FPS:    {1.0 / settings.frame_interval:.2f} (interval={settings.frame_interval}s)",
        f"  JPEG save:     {save_mode}",
        f"  Snapshot dir:  {settings.frame_save_dir}",
        "======================================",
        "",
    ]
    banner = "\n".join(lines)
    print(banner, flush=True)
    logger.info(
        "edge.startup.diagnostics ffmpeg=%s agent_id=%s backend=%s rtsp_enabled=%s mock_mode=%s "
        "anpr_live=%s anpr_debug=%s debug_save_all=%s anpr_hook=%s",
        ffmpeg or "NOT_FOUND",
        agent_id,
        settings.api_base_url,
        settings.rtsp_enabled,
        settings.mock_mode,
        settings.anpr_live_enabled,
        settings.anpr_debug_mode,
        settings.debug_save_all_frames,
        settings.anpr_frame_hook,
    )
    if not settings.mock_mode and not ffmpeg:
        logger.warning(
            "edge.startup.ffmpeg_missing Real RTSP capture requires FFmpeg on PATH. "
            "Camera workers will report connection errors until it is installed."
        )
    if settings.rtsp_enabled and not settings.edge_agent_id:
        logger.warning(
            "edge.startup.missing_credentials EDGE_AGENT_ID / EDGE_AGENT_KEY not set. "
            "Register via POST /api/v1/edge/register and set them in the environment."
        )


def run() -> None:
    settings = get_edge_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    print_startup_diagnostics(settings)

    queue = EventQueue(settings.sqlite_path)
    sync = CloudSync(settings, queue)

    # Backward-compatible Mock ANPR one-shot (no RTSP required).
    if settings.mock_mode and settings.mock_oneshot:
        pipeline = ANPRPipeline()
        result = pipeline.process(frame=None)
        if result.ocr:
            sync.enqueue_detection("mock-camera", result.ocr.text, "ENTRY", result.ocr.confidence)
        try:
            sync.push_pending()
        except Exception:
            pass
        # If RTSP loop is not requested, exit like previous versions.
        if not settings.rtsp_enabled or not settings.edge_agent_id:
            return

    stop = {"flag": False}

    def _handle_signal(*_args: object) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGINT, _handle_signal)
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except (AttributeError, ValueError):
        pass

    live_worker: LiveAnprWorker | None = None
    if settings.anpr_live_enabled:
        live_worker = LiveAnprWorker(settings, sync)
        live_worker.start()
        debug_saver = None
        if settings.anpr_debug_mode and settings.anpr_debug_save_frames:
            debug_saver = DebugSampledFrameSaver(
                settings.anpr_debug_frames_dir,
                fps=settings.anpr_debug_save_fps,
                max_files=settings.anpr_debug_frames_max_files,
                enabled=True,
            )
            logger.info(
                "anpr.debug.frames.enabled dir=%s fps=%s max_files=%s",
                settings.anpr_debug_frames_dir,
                settings.anpr_debug_save_fps,
                settings.anpr_debug_frames_max_files,
            )
        processor = CaptureFrameProcessor(
            live_worker,
            debug_save_all=settings.debug_save_all_frames,
            save_dir=settings.frame_save_dir,
            debug_frame_saver=debug_saver,
        )
    else:
        processor = ANPRSnapshotProcessor(
            save_dir=settings.frame_save_dir,
            debug_save_all=settings.debug_save_all_frames,
            ring_size=settings.frame_ring_size,
            max_files=settings.snapshot_max_files,
            anpr_enabled=settings.anpr_frame_hook,
        )
    manager = CameraManager(settings, processor)

    logger.info(
        "edge.agent.start mock_mode=%s rtsp_enabled=%s anpr_live=%s frame_interval=%s",
        settings.mock_mode,
        settings.rtsp_enabled,
        settings.anpr_live_enabled,
        settings.frame_interval,
    )

    try:
        while not stop["flag"]:
            if settings.rtsp_enabled:
                configs = _load_camera_configs(sync, settings)
                if live_worker is not None:
                    for cfg in configs:
                        live_worker.set_camera_direction(cfg.camera_id, cfg.direction)
                manager.sync_configs(configs)
            else:
                manager.stop_all()

            health = sample_health(queue.size())
            extra = {
                "cpu_usage": health.cpu_usage,
                "memory_usage": health.memory_usage,
                "queue_size": health.queue_size,
                "camera_statuses": manager.health_snapshot(),
            }
            if live_worker is not None:
                extra["anpr_live"] = live_worker.metrics()
            try:
                sync.heartbeat(extra)
            except Exception as exc:  # noqa: BLE001
                logger.warning("edge.heartbeat.failed error=%s", exc)

            try:
                sync.push_pending()
            except Exception:
                pass

            time.sleep(settings.poll_interval_seconds)
    finally:
        if live_worker is not None:
            live_worker.stop()
        manager.stop_all()
        logger.info("edge.agent.shutdown")


def _load_camera_configs(sync: CloudSync, settings: EdgeSettings) -> list[CameraConfig]:
    try:
        cameras = sync.fetch_camera_configs()
    except Exception as exc:  # noqa: BLE001
        logger.warning("edge.cameras.fetch_failed error=%s", exc)
        return []

    configs: list[CameraConfig] = []
    for cam in cameras:
        if not cam.get("streaming"):
            continue
        if not cam.get("enabled", True):
            continue
        rtsp = cam.get("rtsp_url") or ""
        if not rtsp and not settings.mock_mode:
            continue
        direction = str(cam.get("direction") or "ENTRY").upper()
        if direction not in {"ENTRY", "EXIT", "BOTH"}:
            direction = "ENTRY"
        configs.append(
            CameraConfig(
                camera_id=cam["id"],
                rtsp_url=rtsp or f"mock://{cam['id']}",
                enabled=True,
                frame_interval=float(cam.get("frame_interval") or settings.frame_interval),
                reconnect_min_seconds=settings.reconnect_initial_seconds,
                reconnect_max_seconds=settings.reconnect_max_seconds,
                connect_timeout_seconds=settings.connect_timeout_seconds,
                warmup_frames=settings.warmup_frames,
                min_jpeg_bytes=settings.min_jpeg_bytes,
                direction=direction,
            )
        )
    return configs


if __name__ == "__main__":
    run()
