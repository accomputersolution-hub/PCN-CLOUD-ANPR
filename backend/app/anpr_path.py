"""Ensure sibling ``anpr-engine`` is importable without requiring PYTHONPATH.

Manual ANPR / live pipelines import ``pcn_anpr``. Operators often start uvicorn
from ``backend/`` without setting ``PYTHONPATH=../anpr-engine``, which surfaces as
``No module named 'pcn_anpr'`` on ``/manual-anpr/analyze``.
"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_anpr_engine_on_path() -> None:
    try:
        import pcn_anpr  # noqa: F401
        return
    except ImportError:
        pass
    repo_root = Path(__file__).resolve().parents[2]
    engine_dir = repo_root / "anpr-engine"
    if engine_dir.is_dir():
        path = str(engine_dir)
        if path not in sys.path:
            sys.path.insert(0, path)


ensure_anpr_engine_on_path()
