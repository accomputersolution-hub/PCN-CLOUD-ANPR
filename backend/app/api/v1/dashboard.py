from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db, get_tenant, require_permission
from app.core.rbac import Permission
from app.core.runtime import is_firestore
from app.models.anpr_event import AnprEvent
from app.models.camera import Camera
from app.models.enums import CameraStatus, Direction, VisitStatus
from app.models.site import Site
from app.models.vehicle_visit import VehicleVisit
from app.schemas.dashboard import DashboardSummary
from app.schemas.event import EventOut
from app.services import firestore_domain as fs
from app.services.event import serialize_event
from app.services.reports import count_direction, day_bounds
from app.services.tenant import TenantContext

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/summary", response_model=DashboardSummary)
async def summary(
    site_id: str | None = Query(default=None),
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.DASHBOARD_READ)),
) -> DashboardSummary:
    if is_firestore():
        payload = await fs.dashboard_summary_fs(ctx, site_id=site_id)
        payload["recent_events"] = [EventOut.model_validate(e) for e in payload["recent_events"]]
        return DashboardSummary.model_validate(payload)

    assert db is not None
    tz_name = "Asia/Kolkata"
    if site_id:
        ctx.ensure_site(site_id)
        site = await db.get(Site, site_id)
        if site:
            ctx.ensure_org(site.organization_id)
            tz_name = site.timezone
    today = datetime.now(ZoneInfo(tz_name)).date()
    start, end = day_bounds(today, tz_name)

    entries = await count_direction(db, ctx, direction=Direction.ENTRY, start=start, end=end, site_id=site_id)
    exits = await count_direction(db, ctx, direction=Direction.EXIT, start=start, end=end, site_id=site_id)

    det_stmt = select(func.count(AnprEvent.id)).where(AnprEvent.timestamp >= start, AnprEvent.timestamp < end)
    det_stmt = ctx.apply_org(det_stmt, AnprEvent.organization_id)
    det_stmt = ctx.apply_site(det_stmt, AnprEvent.site_id)
    if site_id:
        det_stmt = det_stmt.where(AnprEvent.site_id == site_id)
    detections = int((await db.execute(det_stmt)).scalar_one())

    inside_stmt = select(func.count(VehicleVisit.id)).where(VehicleVisit.status == VisitStatus.CURRENTLY_INSIDE)
    inside_stmt = ctx.apply_org(inside_stmt, VehicleVisit.organization_id)
    inside_stmt = ctx.apply_site(inside_stmt, VehicleVisit.site_id)
    if site_id:
        inside_stmt = inside_stmt.where(VehicleVisit.site_id == site_id)
    inside = int((await db.execute(inside_stmt)).scalar_one())

    cam_stmt = select(Camera)
    cam_stmt = ctx.apply_org(cam_stmt, Camera.organization_id)
    cam_stmt = ctx.apply_site(cam_stmt, Camera.site_id)
    if site_id:
        cam_stmt = cam_stmt.where(Camera.site_id == site_id)
    cameras = (await db.execute(cam_stmt)).scalars().all()
    active = sum(1 for c in cameras if c.status == CameraStatus.ONLINE and c.enabled)
    offline = sum(1 for c in cameras if c.status != CameraStatus.ONLINE)

    recent_stmt = (
        select(AnprEvent)
        .options(selectinload(AnprEvent.camera), selectinload(AnprEvent.gate))
        .order_by(AnprEvent.timestamp.desc())
        .limit(12)
    )
    recent_stmt = ctx.apply_org(recent_stmt, AnprEvent.organization_id)
    recent_stmt = ctx.apply_site(recent_stmt, AnprEvent.site_id)
    if site_id:
        recent_stmt = recent_stmt.where(AnprEvent.site_id == site_id)
    recent = (await db.execute(recent_stmt)).scalars().all()
    site_cache: dict[str, Site] = {}
    events: list[EventOut] = []
    for ev in recent:
        if ev.site_id not in site_cache:
            site_cache[ev.site_id] = await db.get(Site, ev.site_id)  # type: ignore[assignment]
        events.append(EventOut.model_validate(serialize_event(ev, ev.camera, site_cache.get(ev.site_id))))

    return DashboardSummary(
        entries_today=entries,
        exits_today=exits,
        currently_inside=inside,
        detections_today=detections,
        cameras_active=active,
        cameras_offline=offline,
        cameras_total=len(cameras),
        timezone=tz_name,
        recent_events=events,
    )
