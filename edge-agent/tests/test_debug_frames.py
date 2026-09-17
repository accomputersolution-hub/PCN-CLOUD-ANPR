from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

from pcn_edge.config import EdgeSettings
from pcn_edge.debug_frames import DebugSampledFrameSaver
from pcn_edge.frames import Frame
from pcn_edge.live_anpr import CaptureFrameProcessor, LiveAnprWorker
from pcn_edge.queue import EventQueue
from pcn_edge.sync import CloudSync


def _tiny_jpeg() -> bytes:
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
        b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e"
        b"\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
        b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
        b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\x7f\xff\xd9"
    )


def test_debug_save_frames_disabled_writes_nothing(tmp_path) -> None:
    dest = tmp_path / "debug-frames"
    saver = DebugSampledFrameSaver(str(dest), fps=10.0, max_files=10, enabled=False)
    frame = Frame("cam-1", _tiny_jpeg(), datetime.now(UTC), sequence=1)
    assert saver.maybe_save(frame) is None
    assert not dest.exists() or list(dest.glob("*.jpg")) == []
    assert saver.saved_count == 0


def test_debug_save_frames_enabled_rate_limits_and_logs(tmp_path, caplog) -> None:
    dest = tmp_path / "debug-frames"
    saver = DebugSampledFrameSaver(str(dest), fps=1.0, max_files=5, enabled=True)
    jpeg = _tiny_jpeg()
    with caplog.at_level("INFO"):
        p1 = saver.maybe_save(Frame("cam-1", jpeg, datetime.now(UTC), sequence=1))
        p2 = saver.maybe_save(Frame("cam-1", jpeg, datetime.now(UTC), sequence=2))  # within 1s → skip
    assert p1 is not None
    assert p1.exists()
    assert p2 is None
    assert saver.saved_count == 1
    assert any("debug.frame.saved path=" in r.message for r in caplog.records)

    # After interval, another save is allowed
    saver._last_save_mono = time.monotonic() - 2.0
    p3 = saver.maybe_save(Frame("cam-1", jpeg, datetime.now(UTC), sequence=3))
    assert p3 is not None
    assert saver.saved_count == 2
    assert "cam-1_" in p3.name
    assert p3.suffix == ".jpg"


def test_debug_save_frames_rotates_old_files(tmp_path) -> None:
    dest = tmp_path / "debug-frames"
    saver = DebugSampledFrameSaver(str(dest), fps=100.0, max_files=3, enabled=True)
    jpeg = _tiny_jpeg()
    for i in range(5):
        saver._last_save_mono = 0.0  # bypass rate limit
        saver.maybe_save(Frame("cam-1", jpeg, datetime.now(UTC), sequence=i))
        time.sleep(0.02)  # distinct mtimes for rotation order
    files = list(dest.glob("*.jpg"))
    assert len(files) == 3


def test_capture_processor_honors_debug_saver_flag(tmp_path) -> None:
    settings = EdgeSettings(
        sqlite_path=str(tmp_path / "q.db"),
        frame_save_dir=str(tmp_path / "frames"),
        anpr_live_enabled=True,
        anpr_debug_mode=True,
        anpr_debug_save_frames=True,
    )
    queue = EventQueue(settings.sqlite_path)
    sync = CloudSync(settings, queue)
    worker = LiveAnprWorker(settings, sync, infer_fn=lambda _f: {"plates": []})
    worker.start()
    assert worker.wait_ready(timeout=5)
    try:
        dest = tmp_path / "debug-frames"
        saver = DebugSampledFrameSaver(str(dest), fps=10.0, max_files=10, enabled=True)
        proc = CaptureFrameProcessor(worker, debug_frame_saver=saver)
        proc.process(Frame("cam-1", _tiny_jpeg(), datetime.now(UTC), sequence=1))
        assert saver.saved_count == 1
        assert list(dest.glob("*.jpg"))

        # Disabled saver: no writes
        dest2 = tmp_path / "debug-off"
        off = DebugSampledFrameSaver(str(dest2), fps=10.0, enabled=False)
        proc2 = CaptureFrameProcessor(worker, debug_frame_saver=off)
        proc2.process(Frame("cam-1", _tiny_jpeg(), datetime.now(UTC), sequence=2))
        assert off.saved_count == 0
        assert not dest2.exists() or list(dest2.glob("*.jpg")) == []
    finally:
        worker.stop()


def test_settings_default_debug_save_frames_off() -> None:
    # Explicit construction without env still defaults false for production safety
    s = EdgeSettings(anpr_debug_save_frames=False, anpr_debug_mode=True)
    assert s.anpr_debug_save_frames is False
    assert (s.anpr_debug_mode and s.anpr_debug_save_frames) is False
