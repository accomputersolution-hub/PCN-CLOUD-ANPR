from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import client_ip, get_db, get_tenant, require_permission
from app.core.exceptions import NotFoundError
from app.core.rbac import Permission
from app.models.anpr_event import AnprEvent
from app.models.enums import AuditAction, Direction
from app.models.site import Site
from app.schemas.common import PageMeta, Paginated
from app.schemas.event import EventClassifyRequest, EventCorrectRequest, EventOut
from app.services.audit import write_audit
from app.services.event import serialize_event
from app.services.plate import normalize_plate
from app.services.tenant import TenantContext

router = APIRouter(prefix="/events", tags=["events"])


def _filters(
    stmt,
    ctx: TenantContext,
    *,
    plate: str | None,
    direction: Direction | None,
    camera_id: str | None,
    gate_id: str | None,
    site_id: str | None,
    organization_id: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
):
    stmt = ctx.apply_org(stmt, AnprEvent.organization_id)
    stmt = ctx.apply_site(stmt, AnprEvent.site_id)
    if plate:
        compact = normalize_plate(plate).normalized
        stmt = stmt.where(AnprEvent.plate_normalized.contains(compact))
    if direction:
        stmt = stmt.where(AnprEvent.direction == direction)
    if camera_id:
        stmt = stmt.where(AnprEvent.camera_id == camera_id)
    if gate_id:
        stmt = stmt.where(AnprEvent.gate_id == gate_id)
    if site_id:
        ctx.ensure_site(site_id)
        stmt = stmt.where(AnprEvent.site_id == site_id)
    if organization_id:
        ctx.ensure_org(organization_id)
        stmt = stmt.where(AnprEvent.organization_id == organization_id)
    if date_from:
        stmt = stmt.where(AnprEvent.timestamp >= date_from)
    if date_to:
        stmt = stmt.where(AnprEvent.timestamp <= date_to)
    return stmt


@router.get("", response_model=Paginated[EventOut])
async def list_events(
    plate: str | None = None,
    direction: Direction | None = None,
    camera_id: str | None = None,
    gate_id: str | None = None,
    site_id: str | None = None,
    organization_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.EVENT_READ)),
) -> Paginated[EventOut]:
    stmt = select(AnprEvent).options(selectinload(AnprEvent.camera), selectinload(AnprEvent.gate))
    stmt = _filters(
        stmt,
        ctx,
        plate=plate,
        direction=direction,
        camera_id=camera_id,
        gate_id=gate_id,
        site_id=site_id,
        organization_id=organization_id,
        date_from=date_from,
        date_to=date_to,
    )
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (
        await db.execute(stmt.order_by(AnprEvent.timestamp.desc()).offset((page - 1) * page_size).limit(page_size))
    ).scalars().all()
    site_ids = {r.site_id for r in rows}
    sites = {
        s.id: s
        for s in (await db.execute(select(Site).where(Site.id.in_(site_ids)))).scalars().all()
    } if site_ids else {}
    items = [EventOut.model_validate(serialize_event(r, r.camera, sites.get(r.site_id))) for r in rows]
    return Paginated(items=items, meta=PageMeta(total=total, page=page, page_size=page_size))


@router.get("/{event_id}", response_model=EventOut)
async def get_event(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.EVENT_READ)),
) -> EventOut:
    stmt = (
        select(AnprEvent)
        .options(selectinload(AnprEvent.camera), selectinload(AnprEvent.gate))
        .where(AnprEvent.id == event_id)
    )
    event = (await db.execute(stmt)).scalar_one_or_none()
    if not event:
        raise NotFoundError("Event not found")
    ctx.ensure_org(event.organization_id)
    ctx.ensure_site(event.site_id)
    site = await db.get(Site, event.site_id)
    return EventOut.model_validate(serialize_event(event, event.camera, site))


@router.post("/{event_id}/correct", response_model=EventOut)
async def correct_event(
    event_id: str,
    body: EventCorrectRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.EVENT_CORRECT)),
) -> EventOut:
    event = await db.get(AnprEvent, event_id)
    if not event:
        raise NotFoundError("Event not found")
    ctx.ensure_org(event.organization_id)
    previous = event.plate_normalized
    event.plate_text = body.plate_text
    event.plate_normalized = normalize_plate(body.plate_text).normalized
    await write_audit(
        db,
        action=AuditAction.EVENT_CORRECT,
        user_id=ctx.user.id,
        organization_id=event.organization_id,
        ip=client_ip(request),
        target_type="anpr_event",
        target_id=event.id,
        extra={"from": previous, "to": event.plate_normalized},
    )
    await db.commit()
    return await get_event(event_id, db, ctx, _)


@router.post("/{event_id}/classify", response_model=EventOut)
async def classify_event(
    event_id: str,
    body: EventClassifyRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.EVENT_CLASSIFY)),
) -> EventOut:
    event = await db.get(AnprEvent, event_id)
    if not event:
        raise NotFoundError("Event not found")
    ctx.ensure_org(event.organization_id)
    event.classification = body.classification
    event.notes = body.notes
    await write_audit(
        db,
        action=AuditAction.EVENT_CLASSIFY,
        user_id=ctx.user.id,
        organization_id=event.organization_id,
        ip=client_ip(request),
        target_type="anpr_event",
        target_id=event.id,
        extra={"classification": body.classification},
    )
    await db.commit()
    return await get_event(event_id, db, ctx, _)


@router.delete("/{event_id}", status_code=204, response_class=Response)
async def delete_event(
    event_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.EVENT_DELETE)),
) -> Response:
    event = await db.get(AnprEvent, event_id)
    if not event:
        raise NotFoundError("Event not found")
    ctx.ensure_org(event.organization_id)
    await write_audit(
        db,
        action=AuditAction.EVENT_DELETE,
        user_id=ctx.user.id,
        organization_id=event.organization_id,
        ip=client_ip(request),
        target_type="anpr_event",
        target_id=event.id,
    )
    await db.delete(event)
    await db.commit()
    return Response(status_code=204)
