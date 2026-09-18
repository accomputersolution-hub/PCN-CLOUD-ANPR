"""Console step-timing helpers for Manual ANPR / pipeline profiling."""

from __future__ import annotations

import time
from typing import Any


def step_timer() -> float:
    """Wall-clock start mark (``time.time()``) for step duration logging."""
    return time.time()


def elapsed_ms(started: float) -> float:
    return round((time.time() - started) * 1000.0, 2)


def elapsed_s(started: float) -> float:
    return round(time.time() - started, 4)


def print_step(label: str, started: float) -> float:
    """Print one step duration and return elapsed milliseconds."""
    ms = elapsed_ms(started)
    s = elapsed_s(started)
    print(f"[ANPR TIME] {label}: {ms:.2f} ms ({s:.4f} s)", flush=True)
    return ms


def print_timing_summary(title: str, steps: dict[str, float], *, total_ms: float | None = None) -> None:
    """Print a multi-step timing block to the console."""
    print("=" * 60, flush=True)
    print(f"[ANPR TIME] {title}", flush=True)
    print("-" * 60, flush=True)
    for name, ms in steps.items():
        if ms is None:
            continue
        print(f"  {name:40s} {float(ms):10.2f} ms  ({float(ms) / 1000.0:7.4f} s)", flush=True)
    if total_ms is not None:
        print("-" * 60, flush=True)
        print(f"  {'TOTAL':40s} {float(total_ms):10.2f} ms  ({float(total_ms) / 1000.0:7.4f} s)", flush=True)
    print("=" * 60, flush=True)


def merge_timing_into(result: dict[str, Any], steps: dict[str, float], *, total_ms: float | None = None) -> None:
    timing = result.setdefault("timing", {})
    if not isinstance(timing, dict):
        return
    timing["step_breakdown_ms"] = {k: float(v) for k, v in steps.items()}
    if total_ms is not None:
        timing["step_total_ms"] = float(total_ms)
