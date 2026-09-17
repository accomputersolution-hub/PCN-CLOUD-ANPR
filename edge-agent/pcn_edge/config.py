from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


def _frame_interval_from_env() -> float:
    """Sample interval in seconds. Prefer EDGE_SAMPLE_FPS; else EDGE_FRAME_INTERVAL (default 0.5 = 2 FPS)."""
    sample_fps = os.getenv("EDGE_SAMPLE_FPS", "").strip()
    if sample_fps:
        fps = float(sample_fps)
        if fps <= 0:
            raise ValueError("EDGE_SAMPLE_FPS must be > 0")
        return 1.0 / fps
    return float(os.getenv("EDGE_FRAME_INTERVAL", "0.5"))


@dataclass
class EdgeSettings:
    api_base_url: str = os.getenv("API_BASE_URL", "http://localhost:8000")
    edge_agent_id: str = os.getenv("EDGE_AGENT_ID", "")
    edge_agent_key: str = os.getenv("EDGE_AGENT_KEY", "")
    site_id: str = os.getenv("SITE_ID", "")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    sqlite_path: str = os.getenv("EDGE_SQLITE_PATH", "./data/edge-queue.db")
    poll_interval_seconds: float = float(os.getenv("EDGE_POLL_INTERVAL_SECONDS", "5"))
    reconnect_initial_seconds: float = float(
        os.getenv("EDGE_RECONNECT_MIN_SECONDS", os.getenv("EDGE_RECONNECT_INITIAL_SECONDS", "1"))
    )
    reconnect_max_seconds: float = float(os.getenv("EDGE_RECONNECT_MAX_SECONDS", "30"))
    mock_mode: bool = os.getenv("EDGE_MOCK_MODE", "true").lower() == "true"
    rtsp_enabled: bool = os.getenv("EDGE_RTSP_ENABLED", "true").lower() == "true"
    frame_interval: float = _frame_interval_from_env()
    frame_save_dir: str = os.getenv("EDGE_FRAME_SAVE_DIR", "./data/edge-frames")
    connect_timeout_seconds: float = float(os.getenv("EDGE_RTSP_TIMEOUT_SECONDS", "15"))
    # Discard first N decoded frames after FFmpeg open (decoder warm-up).
    warmup_frames: int = int(os.getenv("EDGE_WARMUP_FRAMES", "8"))
    # Reject JPEGs smaller than this (incomplete / gray garbage from old MPEG-4 cams).
    min_jpeg_bytes: int = int(os.getenv("EDGE_MIN_JPEG_BYTES", "1500"))
    # When true, exit after a single mock enqueue (legacy one-shot). Live RTSP always loops.
    mock_oneshot: bool = os.getenv("EDGE_MOCK_ONESHOT", "true").lower() == "true"
    # When true, persist every sampled frame (legacy debug). Default: save only on detection.
    debug_save_all_frames: bool = os.getenv("DEBUG_SAVE_ALL_FRAMES", "false").lower() == "true"
    # Run ANPR hook on each in-memory frame (no DB events). Disable to capture health-only.
    anpr_frame_hook: bool = os.getenv("EDGE_ANPR_FRAME_HOOK", "true").lower() == "true"
    frame_ring_size: int = int(os.getenv("EDGE_FRAME_RING_SIZE", "15"))
    snapshot_max_files: int = int(os.getenv("EDGE_SNAPSHOT_MAX_FILES", "100"))
    # Phase 6B: live RTSP → temporal confirm → edge event
    anpr_live_enabled: bool = os.getenv("ANPR_LIVE_ENABLED", "false").lower() == "true"
    anpr_debug_mode: bool = os.getenv("ANPR_DEBUG_MODE", "false").lower() == "true"
    anpr_confirm_min_observations: int = int(os.getenv("ANPR_CONFIRM_MIN_OBSERVATIONS", "3"))
    anpr_confirm_window_seconds: float = float(os.getenv("ANPR_CONFIRM_WINDOW_SECONDS", "2"))
    anpr_min_ocr_confidence: float = float(os.getenv("ANPR_MIN_OCR_CONFIDENCE", "0.5"))
    anpr_min_plate_confidence: float = float(os.getenv("ANPR_MIN_PLATE_CONFIDENCE", "0.25"))
    anpr_event_cooldown_seconds: float = float(os.getenv("ANPR_EVENT_COOLDOWN_SECONDS", "120"))
    anpr_infer_queue_size: int = int(os.getenv("ANPR_INFER_QUEUE_SIZE", "8"))
    # Debug-only sampled frame dumps (requires ANPR_DEBUG_MODE=true as well)
    anpr_debug_save_frames: bool = os.getenv("ANPR_DEBUG_SAVE_FRAMES", "false").lower() == "true"
    anpr_debug_save_fps: float = float(os.getenv("ANPR_DEBUG_SAVE_FPS", "1"))
    anpr_debug_frames_dir: str = os.getenv("ANPR_DEBUG_FRAMES_DIR", "./data/debug-frames")
    anpr_debug_frames_max_files: int = int(os.getenv("ANPR_DEBUG_FRAMES_MAX_FILES", "100"))


@lru_cache
def get_edge_settings() -> EdgeSettings:
    return EdgeSettings()
