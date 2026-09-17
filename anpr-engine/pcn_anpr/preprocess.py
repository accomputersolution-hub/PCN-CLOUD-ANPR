from __future__ import annotations

"""Plate-crop preprocessing variants for multi-pass OCR.

Designed to reduce right-edge truncation (e.g. MH01EP9019 → MH01EP9) via padding,
upscaling, contrast, sharpening, and optional deskew — without inventing characters.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PreprocessVariant:
    name: str
    image: Any  # BGR or gray ndarray suitable for OCR


def crop_with_padding(
    image: Any,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    pad_ratio: float = 0.18,
    pad_px_min: int = 8,
) -> tuple[Any | None, tuple[int, int, int, int]]:
    """Expand plate bbox before crop. Returns (crop, padded_xyxy)."""
    if image is None:
        return None, (x1, y1, x2, y2)
    h, w = image.shape[:2]
    bw = max(1, x2 - x1)
    bh = max(1, y2 - y1)
    pad_x = max(pad_px_min, int(bw * pad_ratio))
    pad_y = max(pad_px_min, int(bh * pad_ratio))
    # Prefer a bit more padding on the right — truncation is usually right-side
    px1 = max(0, x1 - pad_x)
    py1 = max(0, y1 - pad_y)
    px2 = min(w, x2 + int(pad_x * 1.35))
    py2 = min(h, y2 + pad_y)
    if px2 <= px1 or py2 <= py1:
        return None, (x1, y1, x2, y2)
    return image[py1:py2, px1:px2].copy(), (px1, py1, px2, py2)


def _to_bgr(img: Any) -> Any:
    import cv2
    import numpy as np

    if img is None:
        return None
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.ndim == 3 and img.shape[2] == 1:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    return img


def _upscale(img: Any, scale: float) -> Any:
    import cv2

    if img is None or scale <= 1.01:
        return img
    h, w = img.shape[:2]
    return cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_CUBIC)


def _gray(img: Any) -> Any:
    import cv2

    if img is None:
        return None
    if img.ndim == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _clahe(gray: Any) -> Any:
    import cv2

    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _sharpen(img: Any) -> Any:
    import cv2
    import numpy as np

    if img is None:
        return None
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
    return cv2.filter2D(img, -1, kernel)


def _adaptive_threshold(gray: Any) -> Any:
    import cv2

    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11)


def _deskew(gray: Any) -> Any:
    """Approximate deskew via min-area rect of ink pixels. Falls back to input."""
    import cv2
    import numpy as np

    if gray is None or gray.size < 64:
        return gray
    try:
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        _, bw = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        coords = np.column_stack(np.where(bw > 0))
        if coords.shape[0] < 40:
            return gray
        rect = cv2.minAreaRect(coords)
        angle = rect[-1]
        # OpenCV angle convention
        if angle < -45:
            angle = 90 + angle
        # Keep small corrections only
        if abs(angle) < 0.5 or abs(angle) > 20:
            return gray
        h, w = gray.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
        return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    except Exception:
        return gray


def _perspective_correct(gray: Any) -> Any:
    """Try to warp a quadrilateral plate region to a rectangle when edges are clear."""
    import cv2
    import numpy as np

    if gray is None or min(gray.shape[:2]) < 20:
        return gray
    try:
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blur, 50, 150)
        edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        h, w = gray.shape[:2]
        best = None
        best_area = 0
        for cnt in contours:
            peri = cv2.arcLength(cnt, True)
            approx = cv2.approxPolyDP(cnt, 0.04 * peri, True)
            if len(approx) != 4:
                continue
            area = cv2.contourArea(approx)
            if area < (h * w * 0.15) or area <= best_area:
                continue
            best = approx.reshape(4, 2).astype(np.float32)
            best_area = area
        if best is None:
            return gray
        # Order points: tl, tr, br, bl
        s = best.sum(axis=1)
        diff = np.diff(best, axis=1)
        tl = best[np.argmin(s)]
        br = best[np.argmax(s)]
        tr = best[np.argmin(diff)]
        bl = best[np.argmax(diff)]
        ordered = np.array([tl, tr, br, bl], dtype=np.float32)
        width_a = np.linalg.norm(br - bl)
        width_b = np.linalg.norm(tr - tl)
        height_a = np.linalg.norm(tr - br)
        height_b = np.linalg.norm(tl - bl)
        max_w = int(max(width_a, width_b))
        max_h = int(max(height_a, height_b))
        if max_w < 40 or max_h < 12:
            return gray
        dst = np.array([[0, 0], [max_w - 1, 0], [max_w - 1, max_h - 1], [0, max_h - 1]], dtype=np.float32)
        M = cv2.getPerspectiveTransform(ordered, dst)
        warped = cv2.warpPerspective(gray, M, (max_w, max_h))
        return warped if warped.size > 0 else gray
    except Exception:
        return gray


def generate_ocr_variants(
    plate_bgr: Any,
    *,
    scales: tuple[float, ...] = (2.0, 3.0),
    enable_adaptive: bool = True,
    enable_perspective: bool = True,
) -> list[PreprocessVariant]:
    """Build multiple preprocessed views of a padded plate crop."""
    if plate_bgr is None:
        return []

    variants: list[PreprocessVariant] = []
    # Always include lightly padded upscaled color
    for scale in scales:
        up = _upscale(plate_bgr, scale)
        variants.append(PreprocessVariant(f"color_x{scale:g}", _to_bgr(up)))

        g = _gray(up)
        g_clahe = _clahe(g)
        variants.append(PreprocessVariant(f"gray_clahe_x{scale:g}", _to_bgr(g_clahe)))
        variants.append(PreprocessVariant(f"sharpen_clahe_x{scale:g}", _to_bgr(_sharpen(g_clahe))))

        deskewed = _deskew(g_clahe)
        variants.append(PreprocessVariant(f"deskew_clahe_x{scale:g}", _to_bgr(deskewed)))

        if enable_perspective:
            persp = _perspective_correct(g_clahe)
            variants.append(PreprocessVariant(f"persp_clahe_x{scale:g}", _to_bgr(persp)))

        if enable_adaptive:
            variants.append(PreprocessVariant(f"adaptive_x{scale:g}", _to_bgr(_adaptive_threshold(g_clahe))))
            variants.append(PreprocessVariant(f"deskew_adaptive_x{scale:g}", _to_bgr(_adaptive_threshold(deskewed))))

    return variants


def generate_live_ocr_variants(plate_bgr: Any) -> list[PreprocessVariant]:
    """Single high-value variant for live CPU inference (target <2s/frame).

    Full multipass remains available for offline/batch image ANPR.
    """
    if plate_bgr is None:
        return []
    up = _upscale(plate_bgr, 2.0)
    g = _clahe(_gray(up))
    # Prefer contrast-enhanced gray — best cost/accuracy tradeoff on CPU
    return [
        PreprocessVariant("live_gray_clahe_x2", _to_bgr(g)),
    ]


def save_debug_crops(variants: list[PreprocessVariant], dest_dir: str | Path, prefix: str) -> list[str]:
    """Write each variant as JPEG for inspection. Returns saved paths."""
    try:
        import cv2
    except ImportError:
        return []
    root = Path(dest_dir)
    root.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for v in variants:
        if v.image is None:
            continue
        path = root / f"{prefix}_{v.name}.jpg"
        ok, buf = cv2.imencode(".jpg", v.image, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        if not ok:
            continue
        buf.tofile(str(path))
        saved.append(str(path))
    return saved
