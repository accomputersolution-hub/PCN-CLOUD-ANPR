"""Request-scoped OCR call profiler (observe-only — no accuracy/threshold changes)."""

from __future__ import annotations

import hashlib
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class OcrCallRecord:
    call: int
    stage: str
    crop_id: str
    variant: str
    size: str
    time_ms: float
    fingerprint: str
    duplicate: bool
    model_init_during_call: bool
    raw_preview: str = ""
    confidence: float = 0.0


@dataclass
class OcrCropSession:
    stage: str
    crop_id: str
    bbox: list[int] | None = None
    source: str = ""
    variants_planned: int = 0
    variants_run: int = 0
    early_exited: bool = False
    early_exit_mode: str = ""
    abandon_reason: str | None = None
    calls: int = 0
    total_ms: float = 0.0
    fingerprint: str = ""


def _safe_console_text(text: str, *, limit: int = 40) -> str:
    """Avoid Windows charmap crashes on CJK / odd OCR glyphs in console logs."""
    s = (text or "")[:limit]
    try:
        s.encode("cp1252")
        return s
    except UnicodeEncodeError:
        return s.encode("ascii", errors="replace").decode("ascii")


@dataclass
class OcrPerfSession:
    """Collects every PaddleOCR inference for one process_image / analyze."""

    enabled: bool = True
    calls: list[OcrCallRecord] = field(default_factory=list)
    crops: list[OcrCropSession] = field(default_factory=list)
    fingerprints_seen: dict[str, int] = field(default_factory=dict)
    # fingerprint|variant -> (raw_text, confidence) for skipping duplicate Paddle calls.
    ocr_cache: dict[str, tuple[str, float]] = field(default_factory=dict)
    duplicate_skips: int = 0
    model_was_ready_at_start: bool | None = None
    model_init_ms_during_session: float = 0.0
    model_init_calls: int = 0
    paddle_inference_ms: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _next_call: int = 1

    def note_model_ready(self, ready: bool) -> None:
        if self.model_was_ready_at_start is None:
            self.model_was_ready_at_start = bool(ready)

    def record_model_init(self, init_ms: float) -> None:
        with self._lock:
            self.model_init_calls += 1
            self.model_init_ms_during_session += float(init_ms)

    def cache_ocr(self, fingerprint: str, variant: str, raw: str, confidence: float) -> None:
        key = f"{fingerprint}|{variant}"
        with self._lock:
            self.ocr_cache[key] = (raw or "", float(confidence or 0.0))

    def get_cached_ocr(self, fingerprint: str, variant: str) -> tuple[str, float] | None:
        key = f"{fingerprint}|{variant}"
        with self._lock:
            return self.ocr_cache.get(key)

    def note_duplicate_skip(self) -> None:
        with self._lock:
            self.duplicate_skips += 1

    def stage_call_counts(self) -> dict[str, int]:
        stage1 = sum(1 for c in self.calls if str(c.stage).startswith("stage1"))
        primary = sum(1 for c in self.calls if str(c.stage).startswith("primary_roi"))
        secondary = len(self.calls) - stage1 - primary
        return {
            "stage1_calls": stage1,
            "primary_calls": primary,
            "secondary_calls": max(0, secondary),
            "duplicate_calls": sum(1 for c in self.calls if c.duplicate) + self.duplicate_skips,
            "total_calls": len(self.calls),
        }

    def add_call(
        self,
        *,
        stage: str,
        crop_id: str,
        variant: str,
        size: str,
        time_ms: float,
        fingerprint: str,
        model_init_during_call: bool,
        raw_preview: str = "",
        confidence: float = 0.0,
    ) -> OcrCallRecord:
        with self._lock:
            dup_key = f"{fingerprint}|{variant}"
            duplicate = dup_key in self.fingerprints_seen
            self.fingerprints_seen[dup_key] = self.fingerprints_seen.get(dup_key, 0) + 1
            rec = OcrCallRecord(
                call=self._next_call,
                stage=stage,
                crop_id=crop_id,
                variant=variant,
                size=size,
                time_ms=round(float(time_ms), 2),
                fingerprint=fingerprint[:12],
                duplicate=duplicate,
                model_init_during_call=model_init_during_call,
                raw_preview=_safe_console_text(raw_preview or "", limit=40),
                confidence=float(confidence or 0.0),
            )
            self._next_call += 1
            self.calls.append(rec)
            self.paddle_inference_ms += float(time_ms)
            return rec

    def add_crop(self, crop: OcrCropSession) -> None:
        with self._lock:
            self.crops.append(crop)

    def summary_dict(self) -> dict[str, Any]:
        from pcn_anpr.ocr_ensemble import RESERVED_PRIMARY_OCR_CALLS

        stage1 = [c for c in self.calls if c.stage.startswith("stage1")]
        roi = [c for c in self.calls if c.stage.startswith("primary_roi")]
        other = [c for c in self.calls if c not in stage1 and c not in roi]
        dup_count = sum(1 for c in self.calls if c.duplicate) + self.duplicate_skips
        return {
            "paddle_calls_total": len(self.calls),
            "stage1_calls": len(stage1),
            "stage1_total_ms": round(sum(c.time_ms for c in stage1), 2),
            "primary_roi_calls": len(roi),
            "primary_calls": len(roi),
            "primary_roi_total_ms": round(sum(c.time_ms for c in roi), 2),
            "secondary_calls": len(other),
            "other_calls": len(other),
            "duplicate_calls": dup_count,
            "duplicate_skips": self.duplicate_skips,
            "primary_reserved_budget": RESERVED_PRIMARY_OCR_CALLS,
            "candidate_crops": len(self.crops),
            "model_ready_at_session_start": self.model_was_ready_at_start,
            "model_init_during_process_image": self.model_init_calls > 0,
            "model_init_ms": round(self.model_init_ms_during_session, 2),
            "paddle_inference_total_ms": round(self.paddle_inference_ms, 2),
            "crops": [
                {
                    "stage": c.stage,
                    "crop_id": c.crop_id,
                    "bbox": c.bbox,
                    "source": c.source,
                    "fingerprint": c.fingerprint,
                    "variants_planned": c.variants_planned,
                    "variants_run": c.variants_run,
                    "early_exited": c.early_exited,
                    "early_exit_mode": c.early_exit_mode,
                    "abandon_reason": c.abandon_reason,
                    "calls": c.calls,
                    "total_ms": round(c.total_ms, 2),
                }
                for c in self.crops
            ],
            "calls": [
                {
                    "call": c.call,
                    "stage": c.stage,
                    "crop_id": c.crop_id,
                    "variant": c.variant,
                    "size": c.size,
                    "time_ms": c.time_ms,
                    "fingerprint": c.fingerprint,
                    "duplicate": c.duplicate,
                    "model_init": c.model_init_during_call,
                    "raw": c.raw_preview,
                    "confidence": c.confidence,
                }
                for c in self.calls
            ],
        }

    def print_report(self) -> None:
        print("=" * 72, flush=True)
        print("[OCR PERF] per-call PaddleOCR inference profile", flush=True)
        print("-" * 72, flush=True)
        for c in self.calls:
            dup = " DUP" if c.duplicate else ""
            init = " INIT" if c.model_init_during_call else ""
            print(
                f"[OCR PERF] call={c.call} stage={c.stage} crop={c.crop_id} "
                f"variant={c.variant} size={c.size} time={c.time_ms:.2f}ms "
                f"fp={c.fingerprint}{dup}{init} raw={_safe_console_text(c.raw_preview)!r}",
                flush=True,
            )
        s = self.summary_dict()
        print("-" * 72, flush=True)
        print(f"[OCR PERF] stage1_calls={s['stage1_calls']}", flush=True)
        print(f"[OCR PERF] stage1_total={s['stage1_total_ms']:.2f} ms", flush=True)
        print(f"[OCR PERF] primary_roi_calls={s['primary_roi_calls']}", flush=True)
        print(f"[OCR PERF] primary_roi_total={s['primary_roi_total_ms']:.2f} ms", flush=True)
        print(f"[OCR PERF] candidate_crops={s['candidate_crops']}", flush=True)
        print(f"[OCR PERF] duplicate_calls={s['duplicate_calls']}", flush=True)
        print(
            f"[OCR PERF] model_ready_at_start={s['model_ready_at_session_start']} "
            f"model_init_during_process_image={s['model_init_during_process_image']} "
            f"model_init_ms={s['model_init_ms']:.2f}",
            flush=True,
        )
        print(f"[OCR PERF] paddle_inference_total={s['paddle_inference_total_ms']:.2f} ms", flush=True)
        print(f"[OCR PERF] ocr_total_calls={s['paddle_calls_total']}", flush=True)
        # Overlap: same fingerprint used in both stage1 and primary_roi
        stage1_fps = {c.fingerprint for c in self.calls if c.stage.startswith("stage1")}
        roi_fps = {c.fingerprint for c in self.calls if c.stage.startswith("primary_roi")}
        overlap = stage1_fps & roi_fps
        print(
            f"[OCR PERF] stage1_vs_primary_roi_shared_crop_fps={len(overlap)} "
            f"overlap_fps={sorted(overlap)[:8]}",
            flush=True,
        )
        for crop in self.crops:
            print(
                f"[OCR PERF] crop stage={crop.stage} id={crop.crop_id} "
                f"planned={crop.variants_planned} run={crop.variants_run} "
                f"early_exit={crop.early_exited}/{crop.early_exit_mode} "
                f"abandon={crop.abandon_reason} calls={crop.calls} "
                f"total={crop.total_ms:.2f}ms bbox={crop.bbox}",
                flush=True,
            )
        print("=" * 72, flush=True)


_session_var: ContextVar[OcrPerfSession | None] = ContextVar("ocr_perf_session", default=None)
_meta_var: ContextVar[dict[str, Any]] = ContextVar(
    "ocr_perf_meta",
    default={"stage": "unknown", "crop_id": "unknown", "variant": "unknown", "fingerprint": ""},
)


def get_session() -> OcrPerfSession | None:
    return _session_var.get()


def image_fingerprint(img: Any) -> str:
    """Stable short fingerprint of a crop (for duplicate detection)."""
    try:
        import numpy as np

        if img is None or not hasattr(img, "shape"):
            return "none"
        arr = np.ascontiguousarray(img)
        h, w = int(arr.shape[0]), int(arr.shape[1])
        # Sample bytes — full hash of large crops is fine for plate sizes.
        raw = arr.tobytes()
        digest = hashlib.sha1(raw).hexdigest()
        return f"{w}x{h}_{digest[:10]}"
    except Exception:  # noqa: BLE001
        return "err"


def image_size_str(img: Any) -> str:
    try:
        if img is None or not hasattr(img, "shape"):
            return "?"
        shape = img.shape
        if len(shape) >= 2:
            return f"{int(shape[1])}x{int(shape[0])}x{int(shape[2]) if len(shape) > 2 else 1}"
        return str(shape)
    except Exception:  # noqa: BLE001
        return "?"


@contextmanager
def ocr_perf_session(*, enabled: bool = True) -> Iterator[OcrPerfSession]:
    sess = OcrPerfSession(enabled=enabled)
    token = _session_var.set(sess)
    try:
        yield sess
    finally:
        _session_var.reset(token)


@contextmanager
def ocr_crop_context(
    *,
    stage: str,
    crop_id: str,
    bbox: list[int] | None = None,
    source: str = "",
    image: Any = None,
) -> Iterator[OcrCropSession]:
    sess = get_session()
    crop = OcrCropSession(
        stage=stage,
        crop_id=crop_id,
        bbox=list(bbox) if bbox else None,
        source=source,
        fingerprint=image_fingerprint(image) if image is not None else "",
    )
    meta = {
        "stage": stage,
        "crop_id": crop_id,
        "variant": "unknown",
        "fingerprint": crop.fingerprint,
        "crop_session": crop,
    }
    token = _meta_var.set(meta)
    t0 = time.time()
    try:
        yield crop
    finally:
        crop.total_ms = (time.time() - t0) * 1000.0
        if sess is not None and sess.enabled:
            sess.add_crop(crop)
        _meta_var.reset(token)


@contextmanager
def ocr_variant_context(*, variant: str, image: Any = None) -> Iterator[None]:
    prev = dict(_meta_var.get())
    prev["variant"] = variant
    if image is not None:
        prev["fingerprint"] = image_fingerprint(image)
        prev["size"] = image_size_str(image)
    token = _meta_var.set(prev)
    try:
        yield
    finally:
        _meta_var.reset(token)


def current_meta() -> dict[str, Any]:
    return dict(_meta_var.get())


def note_ensemble_stats(
    *,
    variants_planned: int,
    variants_run: int,
    early_exited: bool,
    early_exit_mode: str,
    abandon_reason: str | None,
) -> None:
    meta = _meta_var.get()
    crop: OcrCropSession | None = meta.get("crop_session")  # type: ignore[assignment]
    if crop is None:
        return
    crop.variants_planned = int(variants_planned)
    crop.variants_run = int(variants_run)
    crop.early_exited = bool(early_exited)
    crop.early_exit_mode = str(early_exit_mode or "")
    crop.abandon_reason = abandon_reason
