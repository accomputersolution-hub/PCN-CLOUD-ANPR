from __future__ import annotations

"""Image load helpers. Never raises for bad paths — callers get None / empty."""

import os
from pathlib import Path
from typing import Any


def resolve_image_path(path: str | Path) -> Path:
    """Normalize a user-supplied image path to an absolute Path.

    - Strips surrounding quotes/whitespace (common when shells nest quotes)
    - Expands ``~``
    - Resolves relative paths against the current working directory
    - Does **not** require the file to exist (caller validates)
    """
    text = os.fspath(path).strip().strip('"').strip("'")
    if not text:
        return Path.cwd() / "__empty_image_path__"
    p = Path(text).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        return p.resolve(strict=False)
    except (OSError, RuntimeError):
        return p.absolute()


def describe_image_path(supplied: str | Path, resolved: Path | None = None) -> dict[str, Any]:
    """Diagnostics for CLI/debug: supplied vs resolved and filesystem checks."""
    resolved = resolved if resolved is not None else resolve_image_path(supplied)
    return {
        "supplied_path": os.fspath(supplied),
        "resolved_path": str(resolved),
        "exists": resolved.exists(),
        "is_file": resolved.is_file(),
        "cwd": str(Path.cwd()),
    }


def load_bgr(path: str | Path) -> Any | None:
    """Load image as BGR ndarray (OpenCV). Returns None if missing/invalid."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return _load_via_pil(path)

    p = resolve_image_path(path)
    if not p.exists() or not p.is_file():
        return None
    # np.fromfile + imdecode: reliable on Windows paths with spaces (unlike cv2.imread)
    data = np.fromfile(os.fspath(p), dtype=np.uint8)
    if data.size == 0:
        return None
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return img


def _load_via_pil(path: str | Path) -> Any | None:
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return None
    p = resolve_image_path(path)
    if not p.exists() or not p.is_file():
        return None
    try:
        with Image.open(p) as im:
            rgb = im.convert("RGB")
            arr = np.asarray(rgb)
            # RGB -> BGR for OpenCV-style consumers
            return arr[:, :, ::-1].copy()
    except Exception:
        return None


def crop_xyxy(image: Any, x1: int, y1: int, x2: int, y2: int) -> Any | None:
    if image is None:
        return None
    h, w = image.shape[:2]
    x1 = max(0, min(w - 1, x1))
    x2 = max(0, min(w, x2))
    y1 = max(0, min(h - 1, y1))
    y2 = max(0, min(h, y2))
    if x2 <= x1 or y2 <= y1:
        return None
    return image[y1:y2, x1:x2].copy()


def bbox_to_xyxy(x: float, y: float, w: float, h: float) -> tuple[int, int, int, int]:
    return int(x), int(y), int(x + w), int(y + h)
