from __future__ import annotations

"""PaddleOCR provider (Apache 2.0). Lazy-import so the engine loads without OCR installed.

Models are loaded once via initialize()/first use and reused for all subsequent reads.
"""

import logging
import re
import time
from typing import Any

from pcn_anpr.interfaces import OCRProvider, OCRResult

logger = logging.getLogger(__name__)


class PaddleOCRProvider(OCRProvider):
    """OCR on a plate crop only — never intended as a full-frame detector."""

    # Process-wide: prove we do not re-construct PaddleOCR per frame
    _construct_count: int = 0
    _ensure_calls: int = 0

    def __init__(self, *, lang: str = "en", use_angle_cls: bool = True, eager: bool = False) -> None:
        self.lang = lang
        self.use_angle_cls = use_angle_cls
        self._ocr: Any | None = None
        self._init_error: str | None = None
        self._init_ms: float | None = None
        self.instance_id = id(self)
        PaddleOCRProvider._construct_count += 1
        if eager:
            self.initialize()

    def initialize(self) -> bool:
        """Eagerly load PaddleOCR once. Safe to call repeatedly."""
        return self._ensure() is not None

    def _ensure(self) -> Any | None:
        PaddleOCRProvider._ensure_calls += 1
        if self._ocr is not None:
            return self._ocr
        if self._init_error:
            return None
        started = time.perf_counter()
        try:
            from paddleocr import PaddleOCR  # type: ignore
        except Exception as exc:  # noqa: BLE001
            self._init_error = f"PaddleOCR not available: {exc}"
            logger.warning("ocr.paddle.unavailable %s", self._init_error)
            return None

        # Try constructor variants across PaddleOCR 2.x / 3.x
        attempts: list[dict[str, Any]] = [
            {"lang": self.lang},
            {"lang": self.lang, "use_angle_cls": self.use_angle_cls},
            {"use_textline_orientation": self.use_angle_cls, "lang": self.lang},
            {},
        ]
        last_err: str | None = None
        for kwargs in attempts:
            try:
                self._ocr = PaddleOCR(**kwargs)
                self._init_ms = (time.perf_counter() - started) * 1000.0
                logger.info(
                    "ocr.paddle.ready kwargs=%s init_ms=%.0f construct_count=%s instance=%s",
                    kwargs or "{}",
                    self._init_ms,
                    PaddleOCRProvider._construct_count,
                    self.instance_id,
                )
                return self._ocr
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
                continue

        self._init_error = last_err or "PaddleOCR init failed"
        logger.warning("ocr.paddle.init_failed %s", self._init_error)
        return None

    @property
    def available(self) -> bool:
        return self._ensure() is not None

    @property
    def init_error(self) -> str | None:
        self._ensure()
        return self._init_error

    @property
    def init_ms(self) -> float | None:
        return self._init_ms

    def read(self, plate_crop: Any) -> OCRResult:
        if plate_crop is None:
            return OCRResult(text="", confidence=0.0, raw_text="")
        ocr = self._ensure()
        if ocr is None:
            return OCRResult(text="", confidence=0.0, raw_text="")

        try:
            import numpy as np

            if hasattr(plate_crop, "shape") and getattr(plate_crop, "size", 1) < 16:
                return OCRResult(text="", confidence=0.0, raw_text="")
            img = plate_crop
            if hasattr(img, "ndim") and img.ndim == 2:
                img = np.stack([img, img, img], axis=-1)

            result = None
            if hasattr(ocr, "ocr"):
                for kwargs in ({"cls": self.use_angle_cls}, {}):
                    try:
                        result = ocr.ocr(img, **kwargs) if kwargs else ocr.ocr(img)
                        break
                    except TypeError:
                        continue
                    except Exception:
                        result = None
            if result is None and hasattr(ocr, "predict"):
                try:
                    result = ocr.predict(img)
                except Exception:
                    result = None
            if result is None:
                return OCRResult(text="", confidence=0.0, raw_text="")
        except Exception:  # noqa: BLE001
            return OCRResult(text="", confidence=0.0, raw_text="")

        return parse_paddle_ocr_result(result)


def parse_paddle_ocr_result(result: Any) -> OCRResult:
    """Parse PaddleOCR raw output into OCRResult. Empty-safe."""
    if not result:
        return OCRResult(text="", confidence=0.0, raw_text="")

    lines: list[tuple[str, float]] = []

    # PaddleOCR 3.x predict() may return list of dicts / Result objects
    if isinstance(result, dict):
        result = [result]

    blocks = result
    if isinstance(result, list) and result and isinstance(result[0], list):
        if result[0] is None:
            blocks = []
        elif result[0] and isinstance(result[0][0], (list, tuple)) and len(result[0][0]) >= 2:
            first = result[0][0]
            if isinstance(first, (list, tuple)) and len(first) == 2 and not isinstance(
                first[0][0] if first else None, (int, float)
            ):
                blocks = result[0]

    for item in blocks or []:
        try:
            if item is None:
                continue
            if hasattr(item, "get") and callable(item.get):
                text = str(item.get("rec_text") or item.get("text") or item.get("transcription") or "")
                conf = float(item.get("rec_score") or item.get("confidence") or item.get("score") or 0.0)
            elif isinstance(item, dict):
                text = str(item.get("rec_text") or item.get("text") or "")
                conf = float(item.get("rec_score") or item.get("confidence") or 0.0)
            else:
                text_info = item[1]
                if isinstance(text_info, (list, tuple)):
                    text = str(text_info[0])
                    conf = float(text_info[1])
                else:
                    text = str(text_info)
                    conf = 0.0
            text = text.strip()
            if text:
                lines.append((text, conf))
        except (IndexError, TypeError, ValueError, AttributeError):
            continue

    if not lines and isinstance(result, list) and result and isinstance(result[0], dict):
        d0 = result[0]
        texts = d0.get("rec_texts") or d0.get("texts") or []
        scores = d0.get("rec_scores") or d0.get("scores") or []
        for i, t in enumerate(texts):
            conf = float(scores[i]) if i < len(scores) else 0.0
            if str(t).strip():
                lines.append((str(t).strip(), conf))

    if not lines:
        return OCRResult(text="", confidence=0.0, raw_text="")

    raw = " ".join(t for t, _ in lines)
    conf = sum(c for _, c in lines) / len(lines)
    compact = re.sub(r"\s+", "", raw)
    return OCRResult(text=compact or raw, confidence=float(conf), raw_text=raw)


class EmptyOCRProvider(OCRProvider):
    """Safe no-op OCR when OCR is disabled or unavailable."""

    def read(self, plate_crop: Any) -> OCRResult:
        return OCRResult(text="", confidence=0.0, raw_text="")

    def initialize(self) -> bool:
        return True
