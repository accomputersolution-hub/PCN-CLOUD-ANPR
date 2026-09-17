from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pcn_edge.config import EdgeSettings
from pcn_edge.frames import DebugFrameProcessor, MockRTSPFrameSource, iter_frames
from pcn_edge.queue import EventQueue
from pcn_edge.reconnect import CameraReconnect
from pcn_edge.rtsp import CameraConfig, RTSPFrameSource, find_ffmpeg, probe_rtsp_connection, redact_url, validate_rtsp_url


def test_queue_survives_duplicate_ids(tmp_path) -> None:
    q = EventQueue(str(tmp_path / "q.db"))
    first = q.enqueue({"id": "evt-1", "plate_text": "MH12AB1234"})
    second = q.enqueue({"id": "evt-1", "plate_text": "MH12AB1234"})
    assert first == second == "evt-1"
    assert len(q.pending()) == 1
    q.mark_synced(["evt-1"])
    assert q.pending() == []


def test_reconnect_backoff_increases() -> None:
    r = CameraReconnect(EdgeSettings(reconnect_initial_seconds=2, reconnect_max_seconds=10))
    d1 = r.record_failure("timeout")
    d2 = r.record_failure("timeout")
    assert d2 >= d1
    r.record_success()
    assert r.state.attempts == 0
    assert r.state.connected is True


def test_find_ffmpeg_uses_path(monkeypatch) -> None:
    monkeypatch.setattr("pcn_edge.rtsp.shutil.which", lambda _: None)
    assert find_ffmpeg() is None
    monkeypatch.setattr("pcn_edge.rtsp.shutil.which", lambda _: r"C:\tools\ffmpeg.exe")
    assert find_ffmpeg() == r"C:\tools\ffmpeg.exe"


def test_startup_diagnostics_banner(capsys, monkeypatch) -> None:
    from pcn_edge.main import print_startup_diagnostics

    monkeypatch.setattr("pcn_edge.main.find_ffmpeg", lambda: r"C:\ffmpeg\bin\ffmpeg.exe")
    settings = EdgeSettings(
        api_base_url="http://localhost:8000",
        edge_agent_id="agent-123",
        mock_mode=False,
        rtsp_enabled=True,
        frame_save_dir="./data/edge-frames",
    )
    print_startup_diagnostics(settings)
    out = capsys.readouterr().out
    assert "FFmpeg:" in out and "FOUND" in out
    assert "agent-123" in out
    assert "http://localhost:8000" in out
    assert "RTSP enabled:  True" in out
    assert "detection snapshots only" in out or "JPEG save" in out


def test_valid_rtsp_configuration() -> None:
    ok, msg = validate_rtsp_url("rtsp://192.168.1.64:554/Streaming/Channels/101")
    assert ok is True
    assert msg == "ok"


def test_invalid_rtsp_url() -> None:
    ok, msg = validate_rtsp_url("http://camera.local/stream")
    assert ok is False
    assert "rtsp" in msg.lower()


def test_connection_failure_without_ffmpeg(monkeypatch) -> None:
    monkeypatch.setattr("pcn_edge.rtsp.shutil.which", lambda _: None)
    # Format still valid — live probe skipped
    result = probe_rtsp_connection("rtsp://10.0.0.1/stream")
    assert result["ok"] is True
    assert result["first_frame_received"] is False

    bad = probe_rtsp_connection("ftp://nope")
    assert bad["ok"] is False


def test_rtsp_source_raises_on_invalid_url() -> None:
    src = RTSPFrameSource(config=CameraConfig(camera_id="c1", rtsp_url="http://bad"))
    with patch("pcn_edge.rtsp.shutil.which", return_value="/usr/bin/ffmpeg"):
        with pytest.raises(ConnectionError):
            src.open()


def test_offline_camera_read_raises(monkeypatch) -> None:
    """FFmpeg exits immediately → ConnectionError (reconnect path)."""
    cfg = CameraConfig(
        camera_id="c1",
        rtsp_url="rtsp://10.255.255.1/offline",
        frame_interval=0.0,
        connect_timeout_seconds=1.0,
        warmup_frames=0,
        min_jpeg_bytes=10,
    )
    src = RTSPFrameSource(config=cfg)
    monkeypatch.setattr("pcn_edge.rtsp.shutil.which", lambda _: "/usr/bin/ffmpeg")

    class _DeadProc:
        def __init__(self) -> None:
            self.stdout = MagicMock()
            self.stdout.read.return_value = b""
            self.stderr = MagicMock()
            self.stderr.readline.return_value = b""
            self._code = 1

        def poll(self):
            return self._code

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

        def wait(self, timeout=None) -> int:
            return self._code

    with patch("pcn_edge.rtsp.subprocess.Popen", return_value=_DeadProc()):
        src.open()
        with pytest.raises(ConnectionError):
            src.read()
        src.close()


def test_continuous_decoder_lifecycle_and_passthrough(monkeypatch) -> None:
    """One long-lived Popen; valid JPEG after warm-up is returned once."""
    jpeg = _padded_jpeg(1800)
    cfg = CameraConfig(
        camera_id="c-cont",
        rtsp_url="rtsp://192.168.1.10/stream",
        frame_interval=0.0,
        connect_timeout_seconds=3.0,
        warmup_frames=2,
        min_jpeg_bytes=1500,
    )
    src = RTSPFrameSource(config=cfg)
    monkeypatch.setattr("pcn_edge.rtsp.shutil.which", lambda _: "/usr/bin/ffmpeg")

    # Three frames: two warm-up + one valid
    stream = jpeg + jpeg + jpeg
    stdout = _FakeStdout(stream)
    stderr = MagicMock()
    stderr.readline.return_value = b""

    class _AliveProc:
        def __init__(self) -> None:
            self.stdout = stdout
            self.stderr = stderr
            self.pid = 4242
            self._alive = True

        def poll(self):
            return None if self._alive and not stdout.closed else 0

        def terminate(self) -> None:
            self._alive = False
            stdout.closed = True

        def kill(self) -> None:
            self._alive = False
            stdout.closed = True

        def wait(self, timeout=None) -> int:
            self._alive = False
            return 0

    pops: list = []

    def fake_popen(cmd, **kwargs):
        pops.append(cmd)
        return _AliveProc()

    with patch("pcn_edge.rtsp.subprocess.Popen", side_effect=fake_popen):
        with patch("pcn_edge.rtsp.is_valid_jpeg_frame", side_effect=_validity_without_luma):
            src.open()
            assert any("-rtsp_transport" in str(c) for c in pops[0])
            assert "tcp" in pops[0]
            assert "image2pipe" in pops[0]
            assert "fps_mode" in pops[0] or "-fps_mode" in pops[0]
            assert "passthrough" in pops[0]
            frame = src.read()
            assert frame is not None
            assert frame.data.startswith(b"\xff\xd8")
            assert frame.sequence == 1
            assert src._warmup_remaining == 0
            src.close()
            assert src.is_open is False


def test_warmup_frames_discarded(monkeypatch) -> None:
    jpeg = _padded_jpeg(1600)
    cfg = CameraConfig(
        camera_id="c-warm",
        rtsp_url="rtsp://192.168.1.10/stream",
        frame_interval=0.0,
        connect_timeout_seconds=3.0,
        warmup_frames=3,
        min_jpeg_bytes=1000,
    )
    src = RTSPFrameSource(config=cfg)
    monkeypatch.setattr("pcn_edge.rtsp.shutil.which", lambda _: "/usr/bin/ffmpeg")
    # 3 warm-up + 1 kept
    stream = jpeg * 4
    proc = _FakeFFmpegProc(_FakeStdout(stream))

    with patch("pcn_edge.rtsp.subprocess.Popen", return_value=proc):
        with patch("pcn_edge.rtsp.is_valid_jpeg_frame", side_effect=_validity_without_luma):
            src.open()
            frame = src.read()
            assert frame is not None
            assert frame.sequence == 1
            src.close()


def test_invalid_frames_discarded_valid_passthrough(monkeypatch) -> None:
    tiny = b"\xff\xd8" + b"\x00" * 20 + b"\xff\xd9"  # below min size
    good = _padded_jpeg(2000)
    cfg = CameraConfig(
        camera_id="c-inv",
        rtsp_url="rtsp://192.168.1.10/stream",
        frame_interval=0.0,
        connect_timeout_seconds=3.0,
        warmup_frames=0,
        min_jpeg_bytes=1500,
    )
    src = RTSPFrameSource(config=cfg)
    monkeypatch.setattr("pcn_edge.rtsp.shutil.which", lambda _: "/usr/bin/ffmpeg")
    stream = tiny + tiny + good
    proc = _FakeFFmpegProc(_FakeStdout(stream))

    with patch("pcn_edge.rtsp.subprocess.Popen", return_value=proc):
        with patch("pcn_edge.rtsp.is_valid_jpeg_frame", side_effect=_validity_without_luma):
            src.open()
            frame = src.read()
            assert frame is not None
            assert len(frame.data) >= 1500
            src.close()


def test_reconnect_after_ffmpeg_failure(monkeypatch) -> None:
    """Worker records failure and backs off when continuous FFmpeg dies."""
    from pcn_edge.camera_worker import CameraWorker
    from pcn_edge.frames import ANPRSnapshotProcessor

    settings = EdgeSettings(
        mock_mode=False,
        frame_save_dir=".",
        reconnect_initial_seconds=0.05,
        reconnect_max_seconds=0.1,
        debug_save_all_frames=False,
        anpr_frame_hook=False,
        frame_interval=0.0,
        warmup_frames=0,
        min_jpeg_bytes=10,
        connect_timeout_seconds=1.0,
    )
    cfg = CameraConfig(
        camera_id="c-fail",
        rtsp_url="rtsp://10.255.255.1/gone",
        frame_interval=0.0,
        reconnect_min_seconds=0.05,
        reconnect_max_seconds=0.1,
        connect_timeout_seconds=1.0,
        warmup_frames=0,
        min_jpeg_bytes=10,
    )

    class _DeadProc:
        def __init__(self) -> None:
            self.stdout = MagicMock()
            self.stdout.read.return_value = b""
            self.stderr = MagicMock()
            self.stderr.readline.return_value = b""
            self.pid = 1

        def poll(self):
            return 1

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

        def wait(self, timeout=None) -> int:
            return 1

    monkeypatch.setattr("pcn_edge.rtsp.shutil.which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr("pcn_edge.camera_worker.RTSPFrameSource.open", lambda self: (_ for _ in ()).throw(ConnectionError("FFmpeg process exited")))

    proc = ANPRSnapshotProcessor(save_dir=".", anpr_enabled=False, debug_save_all=False)
    worker = CameraWorker(cfg, settings, processor=proc, mock=False)
    worker.start()
    import time

    time.sleep(0.25)
    worker.stop()
    assert worker.health.reconnect_count >= 1
    assert worker.health.status in {"OFFLINE", "ERROR", "CONNECTING"}


def test_is_valid_jpeg_frame_rules() -> None:
    from pcn_edge.rtsp import is_valid_jpeg_frame

    assert is_valid_jpeg_frame(b"", min_bytes=10, check_luma=False) is False
    assert is_valid_jpeg_frame(b"notjpeg", min_bytes=3, check_luma=False) is False
    good = _padded_jpeg(1600)
    assert is_valid_jpeg_frame(good, min_bytes=1500, check_luma=False) is True
    tiny = b"\xff\xd8\xff\xd9"
    assert is_valid_jpeg_frame(tiny, min_bytes=1500, check_luma=False) is False


def test_sample_fps_env(monkeypatch) -> None:
    monkeypatch.setenv("EDGE_SAMPLE_FPS", "2")
    monkeypatch.delenv("EDGE_FRAME_INTERVAL", raising=False)
    from pcn_edge.config import _frame_interval_from_env

    assert abs(_frame_interval_from_env() - 0.5) < 1e-9


# --- helpers for continuous FFmpeg fakes ---


def _tiny_jpeg_template() -> bytes:
    return (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
        b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e"
        b"\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
        b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
        b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00"
    )


def _padded_jpeg(min_size: int) -> bytes:
    head = _tiny_jpeg_template()
    pad = max(0, min_size - len(head) - 2)
    return head + (b"\x7f" * pad) + b"\xff\xd9"


class _FakeStdout:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0
        self.closed = False

    def read(self, n: int = -1) -> bytes:
        if self.closed or self._pos >= len(self._data):
            return b""
        if n < 0:
            n = len(self._data) - self._pos
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class _FakeFFmpegProc:
    def __init__(self, stdout: _FakeStdout) -> None:
        self.stdout = stdout
        self.stderr = MagicMock()
        self.stderr.readline.return_value = b""
        self.pid = 99
        self._alive = True

    def poll(self):
        if not self._alive:
            return 0
        if self.stdout._pos >= len(self.stdout._data) and self.stdout.closed:
            return 0
        return None

    def terminate(self) -> None:
        self._alive = False
        self.stdout.closed = True

    def kill(self) -> None:
        self._alive = False
        self.stdout.closed = True

    def wait(self, timeout=None) -> int:
        self._alive = False
        return 0


def _validity_without_luma(data: bytes, *, min_bytes: int = 1500, check_luma: bool = True) -> bool:
    """Size/marker checks only — avoids OpenCV rejecting padded fake JPEGs."""
    from pcn_edge.rtsp import JPEG_EOI, JPEG_SOI

    if not data or len(data) < min_bytes:
        return False
    soi = data.find(JPEG_SOI)
    if soi < 0 or soi > 16:
        return False
    return data[soi:].rfind(JPEG_EOI) >= 0


def test_redact_url_hides_password() -> None:
    redacted = redact_url("rtsp://admin:SuperSecret@192.168.1.10:554/cam/realmonitor?channel=1&subtype=0")
    assert "SuperSecret" not in redacted
    assert "***" in redacted


def test_mock_frame_source_and_debug_processor(tmp_path) -> None:
    src = MockRTSPFrameSource(camera_id="mock-1", interval=0.01)
    proc = DebugFrameProcessor(save_dir=str(tmp_path), save_every_n=1)
    src.open()
    frames = list(iter_frames(src, max_frames=3))
    assert len(frames) == 3
    for f in frames:
        proc.process(f)
        assert f.data.startswith(b"\xff\xd8")
    src.close()
    saved = list(tmp_path.glob("mock-1_*.jpg"))
    assert len(saved) >= 1


def _jpeg_frame(camera_id: str, seq: int) -> "Frame":
    from datetime import UTC, datetime

    from pcn_edge.frames import Frame

    jpeg = (
        b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
        b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e"
        b"\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342"
        b"\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00"
        b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00"
        b"\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
        b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\x7f\xff\xd9"
    )
    return Frame(
        camera_id=camera_id,
        data=jpeg,
        captured_at=datetime.now(UTC),
        width=1,
        height=1,
        sequence=seq,
    )


def test_normal_frame_discarded(tmp_path) -> None:
    from pcn_edge.frames import ANPRSnapshotProcessor

    def no_detection(_frame):
        return {
            "vehicle_detected": False,
            "plate_detected": False,
            "has_ocr_text": False,
            "meaningful_vehicle": False,
            "score": 0.0,
        }

    proc = ANPRSnapshotProcessor(
        save_dir=str(tmp_path),
        debug_save_all=False,
        anpr_hook=no_detection,
        anpr_enabled=True,
    )
    proc.process(_jpeg_frame("cam-a", 1))
    proc.process(_jpeg_frame("cam-a", 2))
    assert proc._discarded == 2
    assert proc._saved == 0
    assert list(tmp_path.glob("*.jpg")) == []


def test_detection_frame_saved(tmp_path) -> None:
    from pcn_edge.frames import ANPRSnapshotProcessor

    def plate_hit(_frame):
        return {
            "vehicle_detected": True,
            "plate_detected": True,
            "has_ocr_text": True,
            "meaningful_vehicle": True,
            "score": 0.9,
            "plates": [{"raw_text": "MH12AB1234", "confidence": 0.9}],
        }

    proc = ANPRSnapshotProcessor(
        save_dir=str(tmp_path),
        debug_save_all=False,
        anpr_hook=plate_hit,
        anpr_enabled=True,
    )
    proc.process(_jpeg_frame("cam-b", 7))
    saved = list(tmp_path.glob("cam-b_*_plate.jpg"))
    assert len(saved) == 1
    assert proc._saved == 1
    assert proc._discarded == 0


def test_debug_mode_saves_every_frame(tmp_path) -> None:
    from pcn_edge.frames import ANPRSnapshotProcessor

    def should_not_run(_frame):
        raise AssertionError("ANPR hook must not run when debug_save_all=True")

    proc = ANPRSnapshotProcessor(
        save_dir=str(tmp_path),
        debug_save_all=True,
        anpr_hook=should_not_run,
        anpr_enabled=True,
    )
    proc.process(_jpeg_frame("cam-c", 1))
    proc.process(_jpeg_frame("cam-c", 2))
    proc.process(_jpeg_frame("cam-c", 3))
    assert len(list(tmp_path.glob("cam-c_*_debug_all.jpg"))) == 3
    assert proc._saved == 3


def test_should_save_snapshot_rules() -> None:
    from pcn_edge.frames import should_save_snapshot

    assert should_save_snapshot({}, debug_save_all=True) is True
    assert should_save_snapshot({"plate_detected": True}) is True
    assert should_save_snapshot({"has_ocr_text": True}) is True
    assert should_save_snapshot({"meaningful_vehicle": True}) is True
    assert should_save_snapshot({"vehicle_detected": True, "meaningful_vehicle": False}) is False


def test_mock_mode_setting_default() -> None:
    s = EdgeSettings(mock_mode=True, rtsp_enabled=False)
    assert s.mock_mode is True


def test_camera_worker_mock_health(tmp_path) -> None:
    from pcn_edge.camera_worker import CameraWorker
    from pcn_edge.frames import ANPRSnapshotProcessor

    settings = EdgeSettings(
        mock_mode=True,
        frame_save_dir=str(tmp_path),
        reconnect_initial_seconds=0.05,
        reconnect_max_seconds=0.1,
        debug_save_all_frames=False,
        anpr_frame_hook=False,
    )
    cfg = CameraConfig(camera_id="w1", rtsp_url="mock://w1", frame_interval=0.05)
    # Health-only: no ANPR / no disk spam
    proc = ANPRSnapshotProcessor(save_dir=str(tmp_path), anpr_enabled=False, debug_save_all=False)
    worker = CameraWorker(cfg, settings, processor=proc, mock=True)
    worker.start()
    import time

    time.sleep(0.35)
    snap = worker.health.to_heartbeat()
    worker.stop()
    assert snap["id"] == "w1"
    assert snap["status"] in {"ONLINE", "CONNECTING", "OFFLINE"}
    assert worker.health.last_frame_at is not None or snap["status"] != "ONLINE"
    assert list(tmp_path.glob("*.jpg")) == []


def test_heartbeat_payload_shape() -> None:
    from pcn_edge.frames import CameraHealth
    from datetime import UTC, datetime

    h = CameraHealth(
        camera_id="c1",
        status="ONLINE",
        last_frame_at=datetime.now(UTC),
        fps=5.0,
        reconnect_count=2,
        last_error=None,
    )
    payload = h.to_heartbeat()
    assert payload["id"] == "c1"
    assert payload["status"] == "ONLINE"
    assert payload["fps"] == 5.0
    assert payload["retry_count"] == 2
