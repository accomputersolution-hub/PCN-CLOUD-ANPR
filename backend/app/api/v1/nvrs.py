from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_db, get_tenant, require_permission
from app.core.rbac import Permission
from app.models.enums import AuditAction
from app.schemas.nvr import NvrCreate, NvrOut, NvrUpdate
from app.services.audit import write_audit
from app.services.nvr import create_nvr, delete_nvr, get_nvr, list_nvrs, update_nvr
from app.services.tenant import TenantContext

router = APIRouter(prefix="/nvrs", tags=["nvrs"])


@router.get("", response_model=list[NvrOut])
async def list_nvr_devices(
    site_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.NVR_READ)),
) -> list[NvrOut]:
    return await list_nvrs(db, ctx, site_id)


@router.post("", response_model=NvrOut, status_code=201)
async def create_nvr_device(
    body: NvrCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.NVR_WRITE)),
) -> NvrOut:
    out = await create_nvr(
        db,
        ctx,
        site_id=body.site_id,
        name=body.name,
        vendor=body.vendor,
        model=body.model,
        host=body.host,
        channel_count=body.channel_count,
        gateway_id=body.gateway_id,
        enabled=body.enabled,
        notes=body.notes,
    )
    await write_audit(
        db,
        action=AuditAction.NVR_CREATE,
        user_id=ctx.user.id,
        organization_id=out.organization_id,
        ip=client_ip(request),
        target_type="nvr",
        target_id=out.id,
    )
    await db.commit()
    return out


@router.get("/{nvr_id}", response_model=NvrOut)
async def get_nvr_device(
    nvr_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.NVR_READ)),
) -> NvrOut:
    return await get_nvr(db, ctx, nvr_id)


@router.patch("/{nvr_id}", response_model=NvrOut)
async def patch_nvr_device(
    nvr_id: str,
    body: NvrUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.NVR_WRITE)),
) -> NvrOut:
    out = await update_nvr(db, ctx, nvr_id, body)
    await write_audit(
        db,
        action=AuditAction.NVR_UPDATE,
        user_id=ctx.user.id,
        organization_id=out.organization_id,
        ip=client_ip(request),
        target_type="nvr",
        target_id=out.id,
    )
    await db.commit()
    return out


@router.delete("/{nvr_id}", status_code=204, response_class=Response)
async def delete_nvr_device(
    nvr_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.NVR_WRITE)),
) -> Response:
    existing = await get_nvr(db, ctx, nvr_id)
    await delete_nvr(db, ctx, nvr_id)
    await write_audit(
        db,
        action=AuditAction.NVR_DELETE,
        user_id=ctx.user.id,
        organization_id=existing.organization_id,
        ip=client_ip(request),
        target_type="nvr",
        target_id=nvr_id,
    )
    await db.commit()
    return Response(status_code=204)
