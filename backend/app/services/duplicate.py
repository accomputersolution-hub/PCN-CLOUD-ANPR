from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.anpr_event import AnprEvent


async def find_duplicate_event(
    db: AsyncSession,
    *,
    site_id: str,
    camera_id: str,
    gate_id: str,
    plate_normalized: str,
    timestamp: datetime,
    duplicate_window_seconds: int,
    event_cooldown_seconds: int,
) -> AnprEvent | None:
    """Same plate at the same camera/gate within the duplicate window or cooldown is one session."""
    window = max(duplicate_window_seconds, event_cooldown_seconds)
    since = timestamp - timedelta(seconds=window)
    stmt: Select[tuple[AnprEvent]] = (
        select(AnprEvent)
        .where(
            AnprEvent.site_id == site_id,
            AnprEvent.camera_id == camera_id,
            AnprEvent.gate_id == gate_id,
            AnprEvent.plate_normalized == plate_normalized,
            AnprEvent.timestamp >= since,
            AnprEvent.timestamp <= timestamp,
        )
        .order_by(AnprEvent.timestamp.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()
