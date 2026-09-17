from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import client_ip, get_db, get_tenant, require_permission
from app.core.exceptions import NotFoundError
from app.core.rbac import Permission
from app.models.anpr_event import AnprEvent
from app.models.enums import AuditAction, VisitStatus
from app.models.site import Site
from app.models.vehicle import Vehicle
from app.models.vehicle_visit import VehicleVisit
from app.schemas.event import EventOut
from app.schemas.vehicle import VehicleDetail, VehicleOut, VehicleVisitorUpdate, VisitOut, VisitResolveRequest
from app.services.audit import write_audit
from app.services.event import serialize_event
from app.services.plate import normalize_plate
from app.services.tenant import TenantContext
from app.services.visit import format_duration

router = APIRouter(tags=["vehicles"])


def _visit_out(v: VehicleVisit) -> VisitOut:
    return VisitOut(
        id=v.id,
        organization_id=v.organization_id,
        site_id=v.site_id,
        vehicle_id=v.vehicle_id,
        plate_normalized=v.plate_normalized,
        entry_event_id=v.entry_event_id,
        exit_event_id=v.exit_event_id,
        entry_at=v.entry_at,
        exit_at=v.exit_at,
        gate_id=v.gate_id,
        duration_seconds=v.duration_seconds,
        status=v.status,
        duration_label=format_duration(v.duration_seconds),
    )


@router.get("/vehicles", response_model=list[VehicleOut])
async def search_vehicles(
    q: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.VEHICLE_READ)),
) -> list[VehicleOut]:
    stmt = select(Vehicle).order_by(Vehicle.last_seen.desc()).limit(100)
    stmt = ctx.apply_org(stmt, Vehicle.organization_id)
    if q:
        compact = normalize_plate(q).normalized
        stmt = stmt.where(Vehicle.plate_normalized.contains(compact))
    rows = (await db.execute(stmt)).scalars().all()
    return [VehicleOut.model_validate(r) for r in rows]


@router.get("/vehicles/{plate}", response_model=VehicleDetail)
async def vehicle_detail(
    plate: str,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.VEHICLE_READ)),
) -> VehicleDetail:
    compact = normalize_plate(plate).normalized
    stmt = select(Vehicle).where(Vehicle.plate_normalized == compact)
    stmt = ctx.apply_org(stmt, Vehicle.organization_id)
    vehicle = (await db.execute(stmt)).scalar_one_or_none()
    if not vehicle:
        raise NotFoundError("Vehicle not found")
    visits = (
        await db.execute(
            select(VehicleVisit)
            .where(VehicleVisit.vehicle_id == vehicle.id)
            .order_by(VehicleVisit.created_at.desc())
        )
    ).scalars().all()
    events = (
        await db.execute(
            select(AnprEvent)
            .options(selectinload(AnprEvent.camera), selectinload(AnprEvent.gate))
            .where(AnprEvent.vehicle_id == vehicle.id)
            .order_by(AnprEvent.timestamp.desc())
        )
    ).scalars().all()
    site_ids = {e.site_id for e in events}
    sites = {
        s.id: s
        for s in (await db.execute(select(Site).where(Site.id.in_(site_ids)))).scalars().all()
    } if site_ids else {}
    return VehicleDetail(
        vehicle=VehicleOut.model_validate(vehicle),
        visits=[_visit_out(v) for v in visits],
        events=[EventOut.model_validate(serialize_event(e, e.camera, sites.get(e.site_id))) for e in events],
    )


@router.patch("/vehicles/{plate}/visitor", response_model=VehicleOut)
async def update_visitor(
    plate: str,
    body: VehicleVisitorUpdate,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.VEHICLE_VISITOR_WRITE)),
) -> VehicleOut:
    compact = normalize_plate(plate).normalized
    stmt = select(Vehicle).where(Vehicle.plate_normalized == compact)
    stmt = ctx.apply_org(stmt, Vehicle.organization_id)
    vehicle = (await db.execute(stmt)).scalar_one_or_none()
    if not vehicle:
        raise NotFoundError("Vehicle not found")
    if body.visitor_note is not None:
        vehicle.visitor_note = body.visitor_note
    if body.classification is not None:
        vehicle.classification = body.classification
    await db.commit()
    await db.refresh(vehicle)
    return VehicleOut.model_validate(vehicle)


@router.post("/visits/{visit_id}/resolve", response_model=VisitOut)
async def resolve_visit(
    visit_id: str,
    body: VisitResolveRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.VISIT_RESOLVE)),
) -> VisitOut:
    visit = await db.get(VehicleVisit, visit_id)
    if not visit:
        raise NotFoundError("Visit not found")
    ctx.ensure_org(visit.organization_id)
    visit.status = body.status
    if body.status == VisitStatus.MANUALLY_RESOLVED:
        vehicle = await db.get(Vehicle, visit.vehicle_id)
        if vehicle:
            vehicle.currently_inside = False
    await write_audit(
        db,
        action=AuditAction.VISIT_RESOLVE,
        user_id=ctx.user.id,
        organization_id=visit.organization_id,
        ip=client_ip(request),
        target_type="vehicle_visit",
        target_id=visit.id,
        extra={"status": body.status, "notes": body.notes},
    )
    await db.commit()
    await db.refresh(visit)
    return _visit_out(visit)
