from __future__ import annotations

"""Phase 6B: temporal confirmation, cooldown, bounded queue, live worker (no real camera)."""

import time
from datetime import UTC, datetime, timedelta

from pcn_edge.config import EdgeSettings
from pcn_edge.frame_queue import BoundedFrameQueue
from pcn_edge.frames import Frame
from pcn_edge.live_anpr import CaptureFrameProcessor, LiveAnprWorker
from pcn_edge.queue import EventQueue
from pcn_edge.sync import CloudSync
from pcn_edge.temporal import EventCoolDown, PlateObservation, TemporalPlateTracker


def _obs(
    plate: str,
    *,
    camera: str = "cam-1",
    conf: float = 0.95,
    plate_conf: float = 0.8,
    ts: datetime | None = None,
    raw: str | None = None,
    seq: int = 1,
) -> PlateObservation:
    return PlateObservation(
        camera_id=camera,
        plate_normalized=plate,
        plate_raw=raw or plate,
        ocr_confidence=conf,
        plate_confidence=plate_conf,
        vehicle_confidence=0.7,
        timestamp=ts or datetime.now(UTC),
        frame_jpeg=b"\xff\xd8fakejpeg\xff\xd9",
        frame_sequence=seq,
        plate_bbox=[10, 10, 100, 40],
        padded_bbox=[5, 5, 105, 45],
        vehicle_bbox=[0, 0, 200, 200],
        processing_ms=12,
    )


def test_consistent_plate_confirmation() -> None:
    tracker = TemporalPlateTracker(min_observations=3, window_seconds=2.0, min_ocr_confidence=0.7)
    t0 = datetime.now(UTC)
    assert tracker.add(_obs("MH01EP9019", conf=0.9, ts=t0, seq=1)) is None
    assert tracker.add(_obs("MH01EP9019", conf=0.95, ts=t0 + timedelta(milliseconds=300), seq=2)) is None
    confirmed = tracker.add(_obs("MH01EP9019", conf=0.99, ts=t0 + timedelta(milliseconds=600), seq=3))
    assert confirmed is not None
    assert confirmed.plate_normalized == "MH01EP9019"
    assert confirmed.observation_count == 3
    assert confirmed.ocr_confidence >= 0.9
    # Highest OCR frame used as snapshot source
    assert confirmed.frame_sequence == 3


def test_inconsistent_ocr_does_not_confirm() -> None:
    tracker = TemporalPlateTracker(min_observations=3, window_seconds=2.0, min_ocr_confidence=0.5)
    t0 = datetime.now(UTC)
    assert tracker.add(_obs("MH01EP9019", ts=t0, seq=1)) is None
    assert tracker.add(_obs("MH01EP9019", ts=t0 + timedelta(milliseconds=200), seq=2)) is None
    # Different normalized plate — does not count toward MH01EP9019
    assert tracker.add(_obs("MH01EP90I9", ts=t0 + timedelta(milliseconds=400), seq=3)) is None
    assert tracker.add(_obs("KA01AB1234", ts=t0 + timedelta(milliseconds=500), seq=4)) is None


def test_low_confidence_rejected() -> None:
    tracker = TemporalPlateTracker(min_observations=3, window_seconds=2.0, min_ocr_confidence=0.8)
    t0 = datetime.now(UTC)
    for i in range(5):
        assert (
            tracker.add(_obs("MH01EP9019", conf=0.5, ts=t0 + timedelta(milliseconds=100 * i), seq=i))
            is None
        )


def test_cooldown_and_duplicate_suppression() -> None:
    cd = EventCoolDown(cooldown_seconds=60)
    t0 = datetime.now(UTC)
    assert cd.allow("cam-1", "MH01EP9019", t0) is True
    cd.mark("cam-1", "MH01EP9019", t0)
    assert cd.allow("cam-1", "MH01EP9019", t0 + timedelta(seconds=10)) is False
    assert cd.allow("cam-1", "MH01EP9019", t0 + timedelta(seconds=61)) is True
    # Different plate not suppressed
    assert cd.allow("cam-1", "KA01AB1234", t0 + timedelta(seconds=10)) is True
    # Different camera not suppressed
    assert cd.allow("cam-2", "MH01EP9019", t0 + timedelta(seconds=10)) is True


def test_bounded_queue_overflow_drops_oldest() -> None:
    q = BoundedFrameQueue(maxsize=2)
    f1 = Frame("c", b"1", datetime.now(UTC), sequence=1)
    f2 = Frame("c", b"2", datetime.now(UTC), sequence=2)
    f3 = Frame("c", b"3", datetime.now(UTC), sequence=3)
    assert q.offer(f1)
    assert q.offer(f2)
    assert q.offer(f3)  # drops f1
    assert q.dropped == 1
    assert q.qsize() == 2
    got = q.get(timeout=0.1)
    assert got is not None and got.sequence == 2
    got2 = q.get(timeout=0.1)
    assert got2 is not None and got2.sequence == 3


def test_bounded_queue_get_latest_drops_stale() -> None:
    q = BoundedFrameQueue(maxsize=8)
    for i in range(1, 5):
        q.offer(Frame("c", b"x", datetime.now(UTC), sequence=i))
    latest = q.get_latest(timeout=0.1)
    assert latest is not None and latest.sequence == 4
    assert q.stale_dropped == 3
    assert q.qsize() == 0


def _fake_plate_result(plate: str = "MH01EP9019", *, ocr_conf: float = 0.95, confident: bool = True) -> dict:
    return {
        "plate_detected": True,
        "vehicle_detected": True,
        "plates": [
            {
                "raw_text": plate,
                "normalized_text": plate if confident else None,
                "normalized_plate": plate if confident else None,
                "ocr_confident": confident,
                "ocr_confidence": ocr_conf,
                "plate_confidence": 0.85,
                "confidence": ocr_conf,
                "bbox": [10, 10, 100, 40],
                "padded_bbox": [5, 5, 105, 45],
            }
        ],
        "vehicles": [{"confidence": 0.7, "bbox": [0, 0, 200, 150]}],
    }


def _submit_and_wait(worker: LiveAnprWorker, frame: Frame, timeout: float = 3.0) -> None:
    """Submit one frame and wait until it is processed (get_latest drops bursts)."""
    before = worker.frames_processed
    worker.submit(frame)
    deadline = time.time() + timeout
    while worker.frames_processed <= before and time.time() < deadline:
        time.sleep(0.02)
    assert worker.frames_processed > before, "frame was not processed"


def _submit_sequential(worker: LiveAnprWorker, frames: list[Frame], *, timeout: float = 5.0) -> None:
    """Submit one frame at a time so get_latest does not collapse observations."""
    for frame in frames:
        before = worker.frames_processed
        worker.submit(frame)
        deadline = time.time() + timeout
        while worker.frames_processed <= before and time.time() < deadline:
            time.sleep(0.01)


def test_live_worker_entry_event(tmp_path) -> None:
    settings = EdgeSettings(
        sqlite_path=str(tmp_path / "q.db"),
        frame_save_dir=str(tmp_path / "frames"),
        anpr_live_enabled=True,
        anpr_confirm_min_observations=3,
        anpr_confirm_window_seconds=5.0,
        anpr_min_ocr_confidence=0.5,
        anpr_event_cooldown_seconds=120,
        anpr_debug_mode=False,
    )
    queue = EventQueue(settings.sqlite_path)
    sync = CloudSync(settings, queue)

    def infer(_frame: Frame) -> dict:
        return _fake_plate_result("MH01EP9019")

    worker = LiveAnprWorker(
        settings,
        sync,
        camera_directions={"cam-entry": "ENTRY"},
        infer_fn=infer,
    )
    worker.start()
    assert worker.wait_ready(timeout=30)
    try:
        t0 = datetime.now(UTC)
        for i in range(3):
            _submit_and_wait(
                worker,
                Frame("cam-entry", b"\xff\xd8x\xff\xd9", t0 + timedelta(milliseconds=200 * i), sequence=i + 1),
            )
        deadline = time.time() + 5
        while worker.events_created < 1 and time.time() < deadline:
            time.sleep(0.05)
        assert worker.events_created == 1
        pending = queue.pending()
        assert len(pending) == 1
        assert pending[0]["plate_text"] == "MH01EP9019"
        assert pending[0]["direction"] == "ENTRY"
        assert pending[0]["source_type"] == "EDGE"
        assert pending[0].get("snapshot_b64")
    finally:
        worker.stop()


def test_live_worker_exit_event(tmp_path) -> None:
    settings = EdgeSettings(
        sqlite_path=str(tmp_path / "q.db"),
        frame_save_dir=str(tmp_path / "frames"),
        anpr_live_enabled=True,
        anpr_confirm_min_observations=2,
        anpr_confirm_window_seconds=5.0,
        anpr_min_ocr_confidence=0.5,
        anpr_event_cooldown_seconds=120,
    )
    queue = EventQueue(settings.sqlite_path)
    sync = CloudSync(settings, queue)

    worker = LiveAnprWorker(
        settings,
        sync,
        camera_directions={"cam-exit": "EXIT"},
        infer_fn=lambda _f: _fake_plate_result("MH12AB1234"),
    )
    worker.start()
    assert worker.wait_ready(timeout=30)
    try:
        t0 = datetime.now(UTC)
        for i in range(2):
            _submit_and_wait(
                worker,
                Frame("cam-exit", b"\xff\xd8x\xff\xd9", t0 + timedelta(milliseconds=100 * i), sequence=i + 1),
            )
        deadline = time.time() + 5
        while worker.events_created < 1 and time.time() < deadline:
            time.sleep(0.05)
        assert worker.events_created == 1
        assert queue.pending()[0]["direction"] == "EXIT"
    finally:
        worker.stop()


def test_duplicate_suppression_after_confirm(tmp_path) -> None:
    settings = EdgeSettings(
        sqlite_path=str(tmp_path / "q.db"),
        frame_save_dir=str(tmp_path / "frames"),
        anpr_live_enabled=True,
        anpr_confirm_min_observations=2,
        anpr_confirm_window_seconds=5.0,
        anpr_min_ocr_confidence=0.5,
        anpr_event_cooldown_seconds=120,
    )
    queue = EventQueue(settings.sqlite_path)
    sync = CloudSync(settings, queue)
    worker = LiveAnprWorker(
        settings,
        sync,
        camera_directions={"cam-1": "ENTRY"},
        infer_fn=lambda _f: _fake_plate_result("MH01EP9019"),
    )
    worker.start()
    assert worker.wait_ready(timeout=30)
    try:
        t0 = datetime.now(UTC)
        for i in range(6):
            _submit_and_wait(
                worker,
                Frame("cam-1", b"\xff\xd8x\xff\xd9", t0 + timedelta(milliseconds=100 * i), sequence=i + 1),
            )
        deadline = time.time() + 5
        while worker.confirmations < 1 and time.time() < deadline:
            time.sleep(0.05)
        time.sleep(0.2)
        assert worker.events_created == 1
        assert len(queue.pending()) == 1
    finally:
        worker.stop()


def test_anpr_inference_failure_does_not_create_event(tmp_path) -> None:
    settings = EdgeSettings(
        sqlite_path=str(tmp_path / "q.db"),
        frame_save_dir=str(tmp_path / "frames"),
        anpr_live_enabled=True,
        anpr_confirm_min_observations=2,
        anpr_confirm_window_seconds=5.0,
    )
    queue = EventQueue(settings.sqlite_path)
    sync = CloudSync(settings, queue)

    def boom(_frame: Frame) -> dict:
        raise RuntimeError("paddle crashed")

    worker = LiveAnprWorker(settings, sync, camera_directions={"cam-1": "ENTRY"}, infer_fn=boom)
    worker.start()
    assert worker.wait_ready(timeout=30)
    try:
        for i in range(3):
            worker.submit(Frame("cam-1", b"\xff\xd8x\xff\xd9", datetime.now(UTC), sequence=i + 1))
        time.sleep(0.5)
        assert worker.infer_failures >= 1
        assert worker.events_created == 0
        assert queue.pending() == []
    finally:
        worker.stop()


def test_capture_continues_when_anpr_fails(tmp_path) -> None:
    """Capture-thread processor must not raise when worker/queue is under stress."""
    settings = EdgeSettings(
        sqlite_path=str(tmp_path / "q.db"),
        frame_save_dir=str(tmp_path / "frames"),
        anpr_live_enabled=True,
        anpr_infer_queue_size=2,
    )
    queue = EventQueue(settings.sqlite_path)
    sync = CloudSync(settings, queue)

    def slow_fail(_frame: Frame) -> dict:
        time.sleep(0.05)
        raise RuntimeError("infer down")

    worker = LiveAnprWorker(settings, sync, infer_fn=slow_fail)
    worker.start()
    proc = CaptureFrameProcessor(worker)
    try:
        for i in range(20):
            # Must never raise — capture loop stays alive
            proc.process(Frame("cam-1", b"\xff\xd8x\xff\xd9", datetime.now(UTC), sequence=i + 1))
        assert worker.queue.dropped >= 0  # may drop under load
        time.sleep(0.4)
        assert worker.infer_failures >= 1
        assert worker.events_created == 0
    finally:
        worker.stop()


def test_low_confidence_live_no_event(tmp_path) -> None:
    settings = EdgeSettings(
        sqlite_path=str(tmp_path / "q.db"),
        frame_save_dir=str(tmp_path / "frames"),
        anpr_live_enabled=True,
        anpr_confirm_min_observations=2,
        anpr_min_ocr_confidence=0.9,
    )
    queue = EventQueue(settings.sqlite_path)
    sync = CloudSync(settings, queue)
    worker = LiveAnprWorker(
        settings,
        sync,
        camera_directions={"cam-1": "ENTRY"},
        infer_fn=lambda _f: _fake_plate_result("MH01EP9019", ocr_conf=0.4, confident=True),
    )
    worker.start()
    assert worker.wait_ready(timeout=30)
    try:
        for i in range(4):
            worker.submit(Frame("cam-1", b"x", datetime.now(UTC), sequence=i))
        time.sleep(0.4)
        assert worker.events_created == 0
    finally:
        worker.stop()


def test_enqueue_detection_payload_fields(tmp_path) -> None:
    settings = EdgeSettings(sqlite_path=str(tmp_path / "q.db"))
    queue = EventQueue(settings.sqlite_path)
    sync = CloudSync(settings, queue)
    eid = sync.enqueue_detection(
        "cam-1",
        "MH01EP9019",
        "ENTRY",
        0.99,
        raw_ocr_text="MH01EP9019",
        plate_detection_confidence=0.88,
        vehicle_detection_confidence=0.7,
        processing_duration_ms=42,
        snapshot_bytes=b"snap",
        plate_crop_bytes=b"plate",
        vehicle_crop_bytes=b"veh",
    )
    row = queue.pending()[0]
    assert row["id"] == eid
    assert row["plate_text"] == "MH01EP9019"
    assert row["direction"] == "ENTRY"
    assert row["ocr_confidence"] == 0.99
    assert row["plate_detection_confidence"] == 0.88
    assert row["processing_duration_ms"] == 42
    assert row["snapshot_b64"]
    assert row["plate_crop_b64"]
    assert row["vehicle_crop_b64"]
    assert row["source_type"] == "EDGE"


def test_unmatched_exit_direction_enqueued(tmp_path) -> None:
    """Edge emits EXIT; backend visit matching creates EXIT_WITHOUT_MATCH (see backend tests)."""
    settings = EdgeSettings(
        sqlite_path=str(tmp_path / "q.db"),
        frame_save_dir=str(tmp_path / "frames"),
        anpr_live_enabled=True,
        anpr_confirm_min_observations=2,
        anpr_confirm_window_seconds=5.0,
        anpr_min_ocr_confidence=0.5,
        anpr_event_cooldown_seconds=10,
    )
    queue = EventQueue(settings.sqlite_path)
    sync = CloudSync(settings, queue)
    worker = LiveAnprWorker(
        settings,
        sync,
        camera_directions={"exit-cam": "EXIT"},
        infer_fn=lambda _f: _fake_plate_result("DL01CA9999"),
    )
    worker.start()
    assert worker.wait_ready(timeout=30)
    try:
        t0 = datetime.now(UTC)
        for i in range(2):
            _submit_and_wait(
                worker,
                Frame("exit-cam", b"\xff\xd8x\xff\xd9", t0 + timedelta(milliseconds=100 * i), sequence=i),
            )
        deadline = time.time() + 5
        while worker.events_created < 1 and time.time() < deadline:
            time.sleep(0.05)
        assert worker.events_created == 1
        evt = queue.pending()[0]
        assert evt["direction"] == "EXIT"
        assert evt["plate_text"] == "DL01CA9999"
    finally:
        worker.stop()
