from __future__ import annotations

"""Pre-OCR plate-crop plausibility checks (geometry + text-like structure).

Rejects obvious road / ground / sky / curb texture before burning OCR time.
Does NOT accept a crop solely because OCR confidence would be high.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PlateCropAssessment:
    score: float
    reject: bool
    reasons: list[str] = field(default_factory=list)
    char_blobs: int = 0
    white_ratio: float = 0.0
    dark_ratio: float = 0.0
    edge_density: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "reject": self.reject,
            "reasons": list(self.reasons),
            "char_blobs": self.char_blobs,
            "white_ratio": round(self.white_ratio, 4),
            "dark_ratio": round(self.dark_ratio, 4),
            "edge_density": round(self.edge_density, 4),
        }


def count_char_like_blobs(gray: Any) -> int:
    """Count dark-on-light glyph-like connected components in a crop."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return 0
    if gray is None or not hasattr(gray, "shape") or gray.size < 16:
        return 0
    h, w = gray.shape[:2]
    if h < 10 or w < 24:
        return 0
    thr = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 12
    )
    thr = cv2.morphologyEx(thr, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    contours, _ = cv2.findContours(thr, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    good = 0
    for cnt in contours:
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bh < h * 0.25 or bh > h * 0.98:
            continue
        if bw < 2 or bw > w * 0.40:
            continue
        aspect = bw / max(bh, 1)
        if aspect > 1.25 or aspect < 0.10:
            continue
        good += 1
    return good


def assess_plate_crop(
    crop: Any,
    *,
    bbox_xyxy: tuple[float, float, float, float] | None = None,
    frame_shape: tuple[int, int] | None = None,
    vehicle_bbox: tuple[float, float, float, float] | None = None,
) -> PlateCropAssessment:
    """Score whether a crop looks like a license-plate region (not road/sky/noise)."""
    reasons: list[str] = []
    if crop is None or not hasattr(crop, "shape") or getattr(crop, "size", 0) < 16:
        return PlateCropAssessment(0.0, True, ["empty_crop"])

    try:
        import cv2
        import numpy as np
    except ImportError:
        return PlateCropAssessment(0.5, False, ["opencv_missing"])

    if crop.ndim == 3:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    else:
        gray = crop
    ch, cw = gray.shape[:2]
    aspect = cw / max(ch, 1)
    # Single-line HSRP ~4–5; two-line motorcycle plates ~1.6–2.8.
    if aspect < 1.2 or aspect > 10.0:
        reasons.append(f"bad_aspect:{aspect:.2f}")
    if ch < 12 or cw < 40:
        reasons.append("too_small")

    mean = float(gray.mean())
    std = float(gray.std())
    white = float((gray > 160).mean())
    dark = float((gray < 90).mean())
    edges = cv2.Canny(gray, 60, 160)
    edge_density = float(edges.mean()) / 255.0
    blobs = count_char_like_blobs(gray)

    # Vertical placement: absolute bottom of frame is usually road under the vehicle.
    cy_norm = None
    if bbox_xyxy is not None and frame_shape is not None and frame_shape[0] > 0:
        y1, y2 = float(bbox_xyxy[1]), float(bbox_xyxy[3])
        cy_norm = ((y1 + y2) * 0.5) / float(frame_shape[0])
        if cy_norm >= 0.88:
            reasons.append(f"bottom_road_zone:{cy_norm:.2f}")
        elif cy_norm <= 0.08:
            reasons.append(f"top_sky_zone:{cy_norm:.2f}")

    # Prefer plate inside lower half of vehicle ROI, not outside it.
    if vehicle_bbox is not None and bbox_xyxy is not None:
        vx1, vy1, vx2, vy2 = vehicle_bbox
        vh = max(vy2 - vy1, 1.0)
        cy = (bbox_xyxy[1] + bbox_xyxy[3]) * 0.5
        rel = (cy - vy1) / vh
        if rel < 0.20 or rel > 0.95:
            reasons.append(f"outside_vehicle_plate_band:{rel:.2f}")

    # Texture: road/gravel often has high edge density but zero glyph blobs.
    if blobs < 2:
        reasons.append(f"few_char_blobs:{blobs}")
    if edge_density > 0.28 and blobs < 3:
        reasons.append(f"chaotic_edges:{edge_density:.2f}")
    if std < 18.0:
        reasons.append(f"flat_texture:{std:.1f}")
    # Pure bright sand / paper without dark glyphs.
    if white > 0.55 and dark < 0.04 and blobs < 3:
        reasons.append("bright_background_no_glyphs")
    # Near-black undercarriage without plate structure.
    if mean < 45 and blobs < 2:
        reasons.append("undercarriage_dark")

    # Vehicle lamp / reflective taillight rejection (night multi-vehicle scenes).
    # White HSRP plates are also bright/low-sat — require few glyphs + little dark ink.
    if crop.ndim == 3:
        hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
        hch, sch, vch = cv2.split(hsv)
        sat_mean = float(sch.mean())
        val_mean = float(vch.mean())
        # Red taillight hue wraps around 0/180.
        # Reject the *light itself* (red-dominated, few ink glyphs). Do NOT reject
        # a neighboring white two-line plate that merely sits under a red lamp —
        # those crops keep dark character ink (dark >= ~0.08) and enough blobs.
        red_mask = ((hch <= 12) | (hch >= 168)) & (sch >= 60) & (vch >= 80)
        red_ratio = float(np.mean(red_mask.astype(np.float32)))
        bright_low_sat = float(np.mean(((vch >= 180) & (sch <= 55)).astype(np.float32)))
        if red_ratio >= 0.28 and blobs < 5 and dark < 0.12:
            reasons.append(f"red_taillight:{red_ratio:.2f}")
        elif red_ratio >= 0.45 and blobs < 4 and dark < 0.10:
            reasons.append(f"red_taillight:{red_ratio:.2f}")
        if bright_low_sat >= 0.55 and blobs < 3 and dark < 0.08:
            reasons.append(f"bright_lamp:{bright_low_sat:.2f}")
        if val_mean >= 185 and sat_mean <= 45 and blobs < 3 and dark < 0.08 and std < 40:
            reasons.append("illumination_dominated")
        # Compact near-square bright blob → head/tail lamp, not a plate.
        # Two-line motorcycle plates are wider (~1.6–3.0) and have dark ink.
        if aspect < 1.8 and mean >= 150 and blobs < 3 and dark < 0.10 and ch >= 24:
            reasons.append("compact_light_blob")
        # Glyph-rich white plate under a taillight: keep (do not escalate to reject).
        if blobs >= 5 and dark >= 0.08 and red_ratio < 0.55:
            reasons[:] = [r for r in reasons if not r.startswith("red_taillight")]
    else:
        if mean >= 185 and std < 35 and blobs < 3 and dark < 0.08:
            reasons.append("illumination_dominated")
        if aspect < 1.8 and mean >= 150 and blobs < 3 and dark < 0.10 and ch >= 24:
            reasons.append("compact_light_blob")

    # Score: glyph structure dominates geometry heuristics.
    blob_score = min(1.0, blobs / 6.0)
    contrast_score = min(1.0, std / 55.0)
    dual_tone = min(1.0, (min(white, 0.55) + min(dark, 0.45)) / 0.7) if blobs >= 2 else 0.2
    # Prefer either classic single-line (~4.2) or two-line motorcycle (~2.2) aspects.
    aspect_score = max(
        1.0 - min(1.0, abs(aspect - 4.2) / 4.2),
        1.0 - min(1.0, abs(aspect - 2.2) / 2.2),
    )
    placement = 1.0
    if cy_norm is not None:
        if 0.35 <= cy_norm <= 0.82:
            placement = 1.0
        elif cy_norm >= 0.88:
            placement = 0.05
        else:
            placement = 0.55

    score = float(
        max(
            0.0,
            min(
                1.0,
                0.45 * blob_score
                + 0.18 * contrast_score
                + 0.15 * dual_tone
                + 0.12 * aspect_score
                + 0.10 * placement,
            ),
        )
    )

    joined = " ".join(reasons)
    hard_reject = blobs < 2 or any(
        key in joined
        for key in (
            "too_small",
            "empty_crop",
            "bright_background_no_glyphs",
            "undercarriage_dark",
            "red_taillight",
            "bright_lamp",
            "illumination_dominated",
            "compact_light_blob",
        )
    )
    # Road under vehicle: high edge chaos / bottom band without glyphs.
    if blobs < 3 and ("bottom_road_zone" in joined or "chaotic_edges" in joined):
        hard_reject = True
    # Strong glyph evidence overrides soft placement warnings — but never
    # overrides explicit vehicle-lamp rejection.
    lamp_hit = any(
        k in joined
        for k in ("red_taillight", "bright_lamp", "illumination_dominated", "compact_light_blob")
    )
    if blobs >= 5 and "bottom_road_zone" not in joined and not lamp_hit:
        hard_reject = any(key in joined for key in ("too_small", "empty_crop"))

    if hard_reject:
        score = min(score, 0.18)

    return PlateCropAssessment(
        score=score,
        reject=hard_reject,
        reasons=reasons,
        char_blobs=blobs,
        white_ratio=white,
        dark_ratio=dark,
        edge_density=edge_density,
    )
