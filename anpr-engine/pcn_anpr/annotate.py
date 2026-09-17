from __future__ import annotations

"""Annotation helpers for debug/CLI output images."""

from pathlib import Path
from typing import Any


def annotate_image(
    image: Any,
    *,
    vehicles: list[dict] | None = None,
    plates: list[dict] | None = None,
) -> Any | None:
    if image is None:
        return None
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    out = image.copy()
    for v in vehicles or []:
        bb = v.get("bbox") or []
        if len(bb) != 4:
            continue
        x1, y1, x2, y2 = map(int, bb)
        cv2.rectangle(out, (x1, y1), (x2, y2), (40, 180, 40), 2)
        cv2.putText(out, "vehicle", (x1, max(15, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (40, 180, 40), 1)

    for p in plates or []:
        bb = p.get("bbox") or []
        if len(bb) != 4:
            continue
        x1, y1, x2, y2 = map(int, bb)
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 165, 255), 2)
        label = p.get("normalized_text") or p.get("raw_text") or "plate"
        conf = p.get("confidence")
        if conf is not None:
            label = f"{label} ({float(conf):.2f})"
        cv2.putText(out, label, (x1, max(15, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 165, 255), 2)
    return out


def save_annotated(image: Any, dest: str | Path) -> Path | None:
    if image is None:
        return None
    try:
        import cv2
    except ImportError:
        return None
    path = Path(dest)
    path.parent.mkdir(parents=True, exist_ok=True)
    # imencode + tofile supports unicode paths on Windows
    ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    if not ok:
        return None
    buf.tofile(str(path))
    return path
