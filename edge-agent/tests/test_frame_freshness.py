from __future__ import annotations

"""Frame content-hash diagnostics (no camera / OCR required)."""

from datetime import UTC, datetime

from pcn_edge.debug_frames import DebugSampledFrameSaver
from pcn_edge.frame_diag import FrameChangeTracker, FrameDiag, content_hash_jpeg
from pcn_edge.frames import Frame


def _make_jpeg_variant(tag: int) -> bytes:
    """Minimal valid-ish JPEG bytes that differ by tag (hash-distinct)."""
    # Build a tiny gray image with OpenCV when available so thumbnails differ.
    try:
        import cv2
        import numpy as np

        img = np.full((48, 64), int(tag) % 200 + 20, dtype=np.uint8)
        img[10:40, 10:50] = (int(tag) * 40) % 255
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        assert ok
        return buf.tobytes()
    except Exception:
        return (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00"
            + bytes([tag % 200]) * 64
            + b"\xff\xc0\x00\x0b\x08\x00\x08\x00\x08\x01\x01\x11\x00"
            b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
            b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00"
            + bytes([tag, (tag * 3) % 255, (tag * 7) % 255])
            + b"\xff\xd9"
        )


def test_three_different_frames_produce_three_hashes() -> None:
    hashes: list[str] = []
    tracker = FrameChangeTracker(warn_after=10)
    for i, tag in enumerate((11, 77, 199), start=1):
        data = _make_jpeg_variant(tag)
        h = content_hash_jpeg(data)
        hashes.append(h)
        tracker.observe(
            FrameDiag(
                sequence=i,
                width=64,
                height=48,
                content_hash=h,
                byte_size=len(data),
            )
        )
    assert len(hashes) == 3
    assert len(set(hashes)) == 3
    assert len(tracker.unique_hashes) == 3


def test_debug_saver_persists_newest_payload_bytes(tmp_path) -> None:
    saver = DebugSampledFrameSaver(str(tmp_path), fps=100.0, max_files=10, enabled=True)
    a = _make_jpeg_variant(1)
    b = _make_jpeg_variant(2)
    c = _make_jpeg_variant(3)
    ha, hb, hc = content_hash_jpeg(a), content_hash_jpeg(b), content_hash_jpeg(c)
    assert len({ha, hb, hc}) == 3

    paths = []
    for i, (data, h) in enumerate(((a, ha), (b, hb), (c, hc)), start=1):
        saver._last_save_mono = 0.0
        p = saver.maybe_save(
            Frame("cam", data, datetime.now(UTC), sequence=i, width=64, height=48, content_hash=h)
        )
        assert p is not None
        paths.append(p)
        assert p.read_bytes() == data
        assert h in p.name

    assert len({p.read_bytes() for p in paths}) == 3


def test_stale_hash_warning(caplog) -> None:
    tracker = FrameChangeTracker(warn_after=3)
    h = "abc123dead00"
    with caplog.at_level("WARNING"):
        for i in range(1, 5):
            tracker.observe(FrameDiag(sequence=i, width=1, height=1, content_hash=h, byte_size=10))
    assert any("frame.stale_hash" in r.message for r in caplog.records)
