from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_db, get_tenant, require_permission
from app.core.rbac import Permission
from app.models.anpr_event import AnprEvent
from app.models.camera import Camera
from app.models.enums import AuditAction, Direction, VisitStatus
from app.models.gate import Gate
from app.models.vehicle_visit import VehicleVisit
from app.schemas.report import ReportResponse, ReportRow
from app.services.audit import write_audit
from app.services.reports import to_csv
from app.services.tenant import TenantContext

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/summary", response_model=ReportResponse)
async def report_summary(
    kind: str = Query("daily", pattern="^(daily|camera|gate|inside)$"),
    from_date: date | None = None,
    to_date: date | None = None,
    site_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.REPORT_READ)),
) -> ReportResponse:
    end = to_date or datetime.now(ZoneInfo("Asia/Kolkata")).date()
    start = from_date or (end - timedelta(days=6))
    start_dt = datetime(start.year, start.month, start.day, tzinfo=ZoneInfo("Asia/Kolkata")).astimezone(ZoneInfo("UTC"))
    end_dt = datetime(end.year, end.month, end.day, tzinfo=ZoneInfo("Asia/Kolkata")).astimezone(ZoneInfo("UTC")) + timedelta(days=1)

    stmt = select(AnprEvent).where(AnprEvent.timestamp >= start_dt, AnprEvent.timestamp < end_dt)
    stmt = ctx.apply_org(stmt, AnprEvent.organization_id)
    stmt = ctx.apply_site(stmt, AnprEvent.site_id)
    if site_id:
        ctx.ensure_site(site_id)
        stmt = stmt.where(AnprEvent.site_id == site_id)
    events = (await db.execute(stmt)).scalars().all()

    rows: list[ReportRow] = []
    if kind == "daily":
        buckets: dict[str, dict[str, int]] = defaultdict(lambda: {"entries": 0, "exits": 0, "detections": 0})
        for ev in events:
            local = ev.local_timestamp.date().isoformat()
            buckets[local]["detections"] += 1
            if ev.direction == Direction.ENTRY:
                buckets[local]["entries"] += 1
            elif ev.direction == Direction.EXIT:
                buckets[local]["exits"] += 1
        rows = [ReportRow(label=k, **v) for k, v in sorted(buckets.items())]
    elif kind == "camera":
        cameras = {c.id: c.name for c in (await db.execute(select(Camera))).scalars().all()}
        buckets = defaultdict(lambda: {"entries": 0, "exits": 0, "detections": 0})
        for ev in events:
            key = cameras.get(ev.camera_id, ev.camera_id)
            buckets[key]["detections"] += 1
            if ev.direction == Direction.ENTRY:
                buckets[key]["entries"] += 1
            elif ev.direction == Direction.EXIT:
                buckets[key]["exits"] += 1
        rows = [ReportRow(label=k, **v) for k, v in sorted(buckets.items())]
    elif kind == "gate":
        gates = {g.id: g.name for g in (await db.execute(select(Gate))).scalars().all()}
        buckets = defaultdict(lambda: {"entries": 0, "exits": 0, "detections": 0})
        for ev in events:
            key = gates.get(ev.gate_id, ev.gate_id)
            buckets[key]["detections"] += 1
            if ev.direction == Direction.ENTRY:
                buckets[key]["entries"] += 1
            elif ev.direction == Direction.EXIT:
                buckets[key]["exits"] += 1
        rows = [ReportRow(label=k, **v) for k, v in sorted(buckets.items())]
    else:
        inside_stmt = select(func.count(VehicleVisit.id)).where(VehicleVisit.status == VisitStatus.CURRENTLY_INSIDE)
        inside_stmt = ctx.apply_org(inside_stmt, VehicleVisit.organization_id)
        inside = int((await db.execute(inside_stmt)).scalar_one())
        rows = [ReportRow(label="Currently inside", entries=inside, exits=0, detections=inside)]

    return ReportResponse(kind=kind, from_date=start, to_date=end, rows=rows)


@router.get("/export")
async def export_csv(
    request: Request,
    kind: str = Query("daily"),
    from_date: date | None = None,
    to_date: date | None = None,
    site_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.REPORT_EXPORT)),
) -> PlainTextResponse:
    data = await report_summary(kind, from_date, to_date, site_id, db, ctx, _)
    csv_body = to_csv(
        ["label", "entries", "exits", "detections"],
        [[r.label, str(r.entries), str(r.exits), str(r.detections)] for r in data.rows],
    )
    await write_audit(
        db,
        action=AuditAction.REPORT_EXPORT,
        user_id=ctx.user.id,
        organization_id=ctx.organization_id,
        ip=client_ip(request),
        target_type="report",
        extra={"kind": kind},
    )
    await db.commit()
    return PlainTextResponse(csv_body, media_type="text/csv")
