from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import client_ip, get_db, get_tenant, require_permission
from app.core.exceptions import NotFoundError
from app.core.rbac import Permission
from app.core.runtime import is_firestore
from app.models.anpr_event import AnprEvent
from app.models.enums import AuditAction, Direction
from app.models.site import Site
from app.models.site_vehicle_registration import SiteVehicleRegistration
from app.schemas.common import PageMeta, Paginated
from app.schemas.event import EventClassifyRequest, EventCorrectRequest, EventOut
from app.services import firestore_domain as fs
from app.services import vehicle_registry as reg_svc
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
    person_name: str | None = None,
    flat_room_unit: str | None = None,
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
    if person_name or flat_room_unit:
        # Live join — do not denormalize registry onto events
        stmt = stmt.join(
            SiteVehicleRegistration,
            and_(
                SiteVehicleRegistration.organization_id == AnprEvent.organization_id,
                SiteVehicleRegistration.site_id == AnprEvent.site_id,
                SiteVehicleRegistration.plate_normalized == AnprEvent.plate_normalized,
            ),
        )
        if person_name:
            stmt = stmt.where(SiteVehicleRegistration.person_name.ilike(f"%{person_name.strip()}%"))
        if flat_room_unit:
            stmt = stmt.where(SiteVehicleRegistration.flat_room_unit.ilike(f"%{flat_room_unit.strip()}%"))
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
    person_name: str | None = None,
    flat_room_unit: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.EVENT_READ)),
) -> Paginated[EventOut]:
    if is_firestore():
        items, total = await fs.list_events(
            ctx,
            site_id=site_id,
            organization_id=organization_id,
            plate=plate,
            direction=direction,
            camera_id=camera_id,
            gate_id=gate_id,
            person_name=person_name,
            flat_room_unit=flat_room_unit,
            page=page,
            page_size=page_size,
        )
        items = await reg_svc.attach_registry_matches_to_event_dicts(db=db, user=ctx.user, items=items)
        return Paginated(
            items=[EventOut.model_validate(i) for i in items],
            meta=PageMeta(total=total, page=page, page_size=page_size),
        )

    assert db is not None
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
        person_name=person_name,
        flat_room_unit=flat_room_unit,
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
    raw_items = [serialize_event(r, r.camera, sites.get(r.site_id)) for r in rows]
    raw_items = await reg_svc.attach_registry_matches_to_event_dicts(db=db, user=ctx.user, items=raw_items)
    items = [EventOut.model_validate(i) for i in raw_items]
    return Paginated(items=items, meta=PageMeta(total=total, page=page, page_size=page_size))


@router.get("/{event_id}", response_model=EventOut)
async def get_event(
    event_id: str,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.EVENT_READ)),
) -> EventOut:
    if is_firestore():
        item = await fs.get_event(ctx, event_id)
        items = await reg_svc.attach_registry_matches_to_event_dicts(db=db, user=ctx.user, items=[item])
        return EventOut.model_validate(items[0])
    assert db is not None
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
    raw = serialize_event(event, event.camera, site)
    raw_items = await reg_svc.attach_registry_matches_to_event_dicts(db=db, user=ctx.user, items=[raw])
    return EventOut.model_validate(raw_items[0])


@router.post("/{event_id}/correct", response_model=EventOut)
async def correct_event(
    event_id: str,
    body: EventCorrectRequest,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.EVENT_CORRECT)),
) -> EventOut:
    if is_firestore():
        from app.repositories import anpr_event_repo

        existing = await anpr_event_repo().get(event_id)
        previous = existing.plate_normalized if existing else None
        out = await fs.correct_event(ctx, event_id, body.plate_text)
        await write_audit(
            db,
            action=AuditAction.EVENT_CORRECT,
            user_id=ctx.user.id,
            organization_id=out["organization_id"],
            ip=client_ip(request),
            target_type="anpr_event",
            target_id=event_id,
            extra={"from": previous, "to": out["plate_normalized"]},
        )
        return EventOut.model_validate(out)

    assert db is not None
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
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.EVENT_CLASSIFY)),
) -> EventOut:
    if is_firestore():
        out = await fs.classify_event(
            ctx, event_id, classification=body.classification, notes=body.notes
        )
        await write_audit(
            db,
            action=AuditAction.EVENT_CLASSIFY,
            user_id=ctx.user.id,
            organization_id=out["organization_id"],
            ip=client_ip(request),
            target_type="anpr_event",
            target_id=event_id,
            extra={"classification": body.classification},
        )
        return EventOut.model_validate(out)

    assert db is not None
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
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.EVENT_DELETE)),
) -> Response:
    if is_firestore():
        from app.repositories import anpr_event_repo

        event = await anpr_event_repo().get(event_id)
        if not event:
            raise NotFoundError("Event not found")
        await write_audit(
            db,
            action=AuditAction.EVENT_DELETE,
            user_id=ctx.user.id,
            organization_id=event.organization_id,
            ip=client_ip(request),
            target_type="anpr_event",
            target_id=event.id,
        )
        await fs.delete_event(ctx, event_id)
        return Response(status_code=204)

    assert db is not None
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
