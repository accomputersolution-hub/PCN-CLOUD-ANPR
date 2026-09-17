from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.anpr_event import AnprEvent
from app.models.enums import Direction
from app.models.vehicle_visit import VehicleVisit
from app.services.tenant import TenantContext


def day_bounds(day: date, tz_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    start = datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(ZoneInfo("UTC"))
    end = start + timedelta(days=1)
    return start, end


async def scoped_events(db: AsyncSession, ctx: TenantContext, stmt: Select) -> Select:
    stmt = ctx.apply_org(stmt, AnprEvent.organization_id)
    stmt = ctx.apply_site(stmt, AnprEvent.site_id)
    return stmt


async def count_direction(
    db: AsyncSession,
    ctx: TenantContext,
    *,
    direction: Direction,
    start: datetime,
    end: datetime,
    site_id: str | None = None,
) -> int:
    stmt = select(func.count(AnprEvent.id)).where(
        AnprEvent.direction == direction,
        AnprEvent.timestamp >= start,
        AnprEvent.timestamp < end,
    )
    stmt = await scoped_events(db, ctx, stmt)
    if site_id:
        stmt = stmt.where(AnprEvent.site_id == site_id)
    return int((await db.execute(stmt)).scalar_one())


def to_csv(headers: list[str], rows: list[list[str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    writer.writerows(rows)
    return buf.getvalue()
