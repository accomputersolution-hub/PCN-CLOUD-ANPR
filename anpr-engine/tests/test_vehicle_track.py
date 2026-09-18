from __future__ import annotations

"""Multi-vehicle track IDs, OCR isolation, and temporal confirmation."""

from datetime import UTC, datetime, timedelta

from pcn_anpr.interfaces import BoundingBox, VehicleDetection
from pcn_anpr.vehicle_track import (
    TrackObservation,
    VehicleTracker,
    assign_frame_local_track_ids,
)


def test_frame_local_track_ids_stable() -> None:
    a = VehicleDetection(BoundingBox(10, 10, 100, 200, 0.8), confidence=0.8)
    b = VehicleDetection(BoundingBox(400, 100, 200, 150, 0.6), confidence=0.6)
    tagged = assign_frame_local_track_ids([a, b])
    assert tagged[0].track_id == "v1"
    assert tagged[1].track_id == "v2"


def test_tracker_keeps_ids_across_frames() -> None:
    tr = VehicleTracker(iou_match=0.2, max_misses=3)
    f1 = [
        VehicleDetection(BoundingBox(100, 100, 200, 300, 0.8), confidence=0.8),
        VehicleDetection(BoundingBox(500, 200, 180, 120, 0.5), confidence=0.5),
    ]
    out1 = tr.update(f1, primary_index=0)
    id_a, id_b = out1[0].track_id, out1[1].track_id
    assert id_a and id_b and id_a != id_b

    f2 = [
        VehicleDetection(BoundingBox(110, 105, 200, 300, 0.8), confidence=0.8),
        VehicleDetection(BoundingBox(510, 205, 180, 120, 0.5), confidence=0.5),
    ]
    out2 = tr.update(f2, primary_index=0)
    assert out2[0].track_id == id_a
    assert out2[1].track_id == id_b


def test_ocr_history_isolated_per_track() -> None:
    tr = VehicleTracker(min_confirm_observations=2, confirm_window_seconds=5.0, min_ocr_confidence=0.5)
    now = datetime.now(UTC)
    # Seed tracks
    tr.update(
        [
            VehicleDetection(BoundingBox(100, 100, 200, 300, 0.8), confidence=0.8, track_id="v1"),
            VehicleDetection(BoundingBox(500, 200, 180, 120, 0.5), confidence=0.5, track_id="v2"),
        ],
        primary_index=0,
        now=now,
    )
    # Force known IDs
    tr._tracks.clear()
    from pcn_anpr.vehicle_track import TrackState

    tr._tracks["bike"] = TrackState("bike", (100, 100, 300, 400), 0.8, is_primary=True)
    tr._tracks["car"] = TrackState("car", (500, 200, 680, 320), 0.5, is_primary=False)

    c1 = tr.note_plate(
        TrackObservation(
            track_id="bike",
            plate_normalized="MH12AB5687",
            plate_raw="MH12AB5687",
            ocr_confidence=0.9,
            plate_confidence=0.8,
            timestamp=now,
            on_primary=True,
        )
    )
    assert c1 is None
    # Car plate must not confirm the bike plate
    c2 = tr.note_plate(
        TrackObservation(
            track_id="car",
            plate_normalized="MH14KX7023",
            plate_raw="MH14KX7023",
            ocr_confidence=0.99,
            plate_confidence=0.9,
            timestamp=now + timedelta(milliseconds=50),
            on_primary=False,
        )
    )
    assert c2 is None
    confirmed_bike = tr.note_plate(
        TrackObservation(
            track_id="bike",
            plate_normalized="MH12AB5687",
            plate_raw="MH12AB5687",
            ocr_confidence=0.91,
            plate_confidence=0.8,
            timestamp=now + timedelta(milliseconds=100),
            on_primary=True,
        )
    )
    assert confirmed_bike is not None
    assert confirmed_bike.plate_normalized == "MH12AB5687"
    assert confirmed_bike.track_id == "bike"
    # Car still needs a second matching obs of MH14
    assert tr._tracks["car"].best_plate == "MH14KX7023"
    assert all(o.plate_normalized != "MH12AB5687" for o in tr._tracks["car"].ocr_history)


def test_track_survives_missed_plate_frames() -> None:
    tr = VehicleTracker(iou_match=0.2, max_misses=3)
    now = datetime.now(UTC)
    out = tr.update(
        [VehicleDetection(BoundingBox(100, 100, 200, 300, 0.8), confidence=0.8)],
        primary_index=0,
        now=now,
    )
    tid = out[0].track_id
    # Empty detection frame — track kept with miss counter
    tr.update([], now=now + timedelta(milliseconds=100))
    assert tid in tr.tracks
    assert tr.tracks[tid].misses == 1
    # Reappear
    out2 = tr.update(
        [VehicleDetection(BoundingBox(105, 102, 200, 300, 0.8), confidence=0.8)],
        primary_index=0,
        now=now + timedelta(milliseconds=200),
    )
    assert out2[0].track_id == tid
    assert tr.tracks[tid].misses == 0
