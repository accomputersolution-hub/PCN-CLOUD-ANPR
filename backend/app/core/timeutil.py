"""Timezone helpers.

SQLite (and some drivers) drop tzinfo on DateTime(timezone=True) round-trips.
Always normalize before comparing against aware ``datetime.now(UTC)``.
"""

from __future__ import annotations

from datetime import UTC, datetime


def ensure_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime. Naive values are treated as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
