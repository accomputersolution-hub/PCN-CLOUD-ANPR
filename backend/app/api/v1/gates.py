from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_db, get_tenant, require_permission
from app.core.exceptions import NotFoundError
from app.core.rbac import Permission
from app.models.enums import AuditAction
from app.models.gate import Gate
from app.models.site import Site
from app.schemas.gate import GateCreate, GateOut, GateUpdate
from app.services.audit import write_audit
from app.services.tenant import TenantContext

router = APIRouter(prefix="/gates", tags=["gates"])


@router.get("", response_model=list[GateOut])
async def list_gates(
    site_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.GATE_READ)),
) -> list[GateOut]:
    stmt = select(Gate).order_by(Gate.name)
    stmt = ctx.apply_org(stmt, Gate.organization_id)
    stmt = ctx.apply_site(stmt, Gate.site_id)
    if site_id:
        ctx.ensure_site(site_id)
        stmt = stmt.where(Gate.site_id == site_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [GateOut.model_validate(r) for r in rows]


@router.post("", response_model=GateOut, status_code=201)
async def create_gate(
    body: GateCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.GATE_WRITE)),
) -> GateOut:
    site = await db.get(Site, body.site_id)
    if not site:
        raise NotFoundError("Site not found")
    ctx.ensure_org(site.organization_id)
    ctx.ensure_site(site.id)
    gate = Gate(organization_id=site.organization_id, site_id=site.id, name=body.name, mode=body.mode)
    db.add(gate)
    await db.flush()
    await write_audit(
        db,
        action=AuditAction.GATE_CREATE,
        user_id=ctx.user.id,
        organization_id=site.organization_id,
        ip=client_ip(request),
        target_type="gate",
        target_id=gate.id,
    )
    await db.commit()
    await db.refresh(gate)
    return GateOut.model_validate(gate)


@router.patch("/{gate_id}", response_model=GateOut)
async def update_gate(
    gate_id: str,
    body: GateUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.GATE_WRITE)),
) -> GateOut:
    gate = await db.get(Gate, gate_id)
    if not gate:
        raise NotFoundError("Gate not found")
    ctx.ensure_org(gate.organization_id)
    ctx.ensure_site(gate.site_id)
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(gate, key, value)
    await write_audit(
        db,
        action=AuditAction.GATE_UPDATE,
        user_id=ctx.user.id,
        organization_id=gate.organization_id,
        ip=client_ip(request),
        target_type="gate",
        target_id=gate.id,
    )
    await db.commit()
    await db.refresh(gate)
    return GateOut.model_validate(gate)


@router.delete("/{gate_id}", status_code=204, response_class=Response)
async def delete_gate(
    gate_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.GATE_WRITE)),
) -> Response:
    gate = await db.get(Gate, gate_id)
    if not gate:
        raise NotFoundError("Gate not found")
    ctx.ensure_org(gate.organization_id)
    await write_audit(
        db,
        action=AuditAction.GATE_DELETE,
        user_id=ctx.user.id,
        organization_id=gate.organization_id,
        ip=client_ip(request),
        target_type="gate",
        target_id=gate.id,
    )
    await db.delete(gate)
    await db.commit()
    return Response(status_code=204)
