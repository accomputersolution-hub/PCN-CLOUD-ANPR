from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.anpr_event import AnprEvent
from app.models.enums import Direction, VisitStatus
from app.models.vehicle import Vehicle
from app.models.vehicle_visit import VehicleVisit


def format_duration(seconds: int | None) -> str | None:
    if seconds is None:
        return None
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


async def apply_visit_match(
    db: AsyncSession,
    *,
    event: AnprEvent,
    vehicle: Vehicle,
) -> VehicleVisit:
    if event.direction == Direction.ENTRY:
        return await _handle_entry(db, event, vehicle)
    if event.direction == Direction.EXIT:
        return await _handle_exit(db, event, vehicle)
    return await _handle_entry(db, event, vehicle)


async def _open_visit(db: AsyncSession, vehicle: Vehicle, site_id: str) -> VehicleVisit | None:
    stmt = (
        select(VehicleVisit)
        .where(
            VehicleVisit.vehicle_id == vehicle.id,
            VehicleVisit.site_id == site_id,
            VehicleVisit.status == VisitStatus.CURRENTLY_INSIDE,
        )
        .order_by(VehicleVisit.entry_at.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _handle_entry(db: AsyncSession, event: AnprEvent, vehicle: Vehicle) -> VehicleVisit:
    existing = await _open_visit(db, vehicle, event.site_id)
    if existing:
        event.visit_id = existing.id
        vehicle.last_seen = event.timestamp
        vehicle.currently_inside = True
        return existing
    visit = VehicleVisit(
        organization_id=event.organization_id,
        site_id=event.site_id,
        vehicle_id=vehicle.id,
        plate_normalized=event.plate_normalized,
        entry_event_id=event.id,
        entry_at=event.timestamp,
        gate_id=event.gate_id,
        status=VisitStatus.CURRENTLY_INSIDE,
    )
    db.add(visit)
    await db.flush()
    event.visit_id = visit.id
    vehicle.currently_inside = True
    vehicle.last_seen = event.timestamp
    vehicle.total_visits += 1
    return visit


async def _handle_exit(db: AsyncSession, event: AnprEvent, vehicle: Vehicle) -> VehicleVisit:
    existing = await _open_visit(db, vehicle, event.site_id)
    if existing:
        existing.exit_event_id = event.id
        existing.exit_at = event.timestamp
        existing.status = VisitStatus.COMPLETED
        if existing.entry_at and event.timestamp:
            entry_at = existing.entry_at
            exit_at = event.timestamp
            # Normalize naive/aware mismatch from mixed DB drivers.
            if entry_at.tzinfo is None and exit_at.tzinfo is not None:
                entry_at = entry_at.replace(tzinfo=exit_at.tzinfo)
            elif exit_at.tzinfo is None and entry_at.tzinfo is not None:
                exit_at = exit_at.replace(tzinfo=entry_at.tzinfo)
            existing.duration_seconds = int((exit_at - entry_at).total_seconds())
        event.visit_id = existing.id
        vehicle.currently_inside = False
        vehicle.last_seen = event.timestamp
        return existing

    # EXIT WITHOUT MATCH — do not invent an entry.
    visit = VehicleVisit(
        organization_id=event.organization_id,
        site_id=event.site_id,
        vehicle_id=vehicle.id,
        plate_normalized=event.plate_normalized,
        exit_event_id=event.id,
        exit_at=event.timestamp,
        gate_id=event.gate_id,
        status=VisitStatus.EXIT_WITHOUT_MATCH,
    )
    db.add(visit)
    await db.flush()
    event.visit_id = visit.id
    vehicle.currently_inside = False
    vehicle.last_seen = event.timestamp
    return visit


def visit_duration_label(entry_at: datetime | None, exit_at: datetime | None) -> str | None:
    if not entry_at or not exit_at:
        return None
    return format_duration(int((exit_at - entry_at).total_seconds()))
