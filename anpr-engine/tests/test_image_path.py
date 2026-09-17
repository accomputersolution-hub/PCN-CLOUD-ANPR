from __future__ import annotations

"""Regression: Windows absolute image paths with spaces must resolve and load."""

from pathlib import Path

from pcn_anpr.config import ANPRSettings
from pcn_anpr.cli import main as cli_main
from pcn_anpr.factory import build_pipeline
from pcn_anpr.image_io import describe_image_path, load_bgr, resolve_image_path


def _write_tiny_jpeg(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import cv2
        import numpy as np

        img = np.zeros((48, 64, 3), dtype=np.uint8)
        img[:] = (40, 80, 120)
        img[10:40, 10:50] = (200, 200, 200)
        ok, buf = cv2.imencode(".jpg", img)
        assert ok
        path.write_bytes(buf.tobytes())
    except Exception:
        # Minimal JPEG SOI/EOI fallback
        path.write_bytes(
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00"
            + bytes([8]) * 64
            + b"\xff\xc0\x00\x0b\x08\x00\x08\x00\x08\x01\x01\x11\x00"
            b"\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00"
            b"\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b"
            b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00\x7f\xff\xd9"
        )


def test_resolve_absolute_windows_path_with_spaces(tmp_path: Path) -> None:
    # Mirror the real project layout: "...\car managment\..."
    root = tmp_path / "StudioProjects" / "car managment" / "edge-agent" / "data" / "debug-frames"
    image = root / "test_image.jpg"
    _write_tiny_jpeg(image)

    # Absolute path string as a CLI/user would pass it
    supplied = str(image)
    assert "car managment" in supplied
    assert " " in supplied

    resolved = resolve_image_path(supplied)
    assert resolved.is_absolute()
    assert resolved.exists()
    assert resolved.is_file()
    assert resolved == image.resolve()

    info = describe_image_path(supplied, resolved)
    assert info["exists"] is True
    assert info["is_file"] is True

    frame = load_bgr(supplied)
    assert frame is not None
    assert hasattr(frame, "shape")
    assert frame.shape[0] > 0 and frame.shape[1] > 0

    pipe = build_pipeline(ANPRSettings(provider_mode="mock"))
    result = pipe.process_image(supplied)
    assert result.get("error") not in {"image_not_found", "invalid_image"}
    assert result.get("decoded_shape") is not None


def test_cli_accepts_absolute_path_with_spaces(tmp_path: Path) -> None:
    root = tmp_path / "car managment" / "edge-agent" / "data" / "debug-frames"
    image = root / "test_image.jpg"
    _write_tiny_jpeg(image)
    out = tmp_path / "out"

    code = cli_main(
        [
            "--image",
            str(image),
            "--output-dir",
            str(out),
            "--provider",
            "mock",
            "--no-annotate",
            "--debug",
        ]
    )
    assert code == 0
    assert (out / "results.json").exists()
    text = (out / "results.json").read_text(encoding="utf-8")
    assert "image_not_found" not in text


def test_quoted_path_is_stripped(tmp_path: Path) -> None:
    image = tmp_path / "car managment" / "frame.jpg"
    _write_tiny_jpeg(image)
    quoted = f'"{image}"'
    resolved = resolve_image_path(quoted)
    assert resolved.is_file()
    assert load_bgr(quoted) is not None
