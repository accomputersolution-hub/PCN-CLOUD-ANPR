from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_db, get_tenant, require_permission
from app.core.exceptions import NotFoundError, ValidationAppError
from app.core.rbac import Permission
from app.core.runtime import is_firestore
from app.models.enums import AuditAction
from app.models.site import DEFAULT_SITE_SETTINGS, Site
from app.schemas.connectivity import SiteConnectivityOut, SiteConnectivityUpdate
from app.schemas.site import SiteCreate, SiteOut, SiteUpdate
from app.services import firestore_domain as fs
from app.services.audit import write_audit
from app.services.gateway import site_connectivity, update_site_connectivity
from app.services.tenant import TenantContext

router = APIRouter(prefix="/sites", tags=["sites"])


@router.get("", response_model=list[SiteOut])
async def list_sites(
    organization_id: str | None = None,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.SITE_READ)),
) -> list[SiteOut]:
    if is_firestore():
        rows = await fs.list_sites(ctx, organization_id=organization_id)
        return [SiteOut.model_validate(r) for r in rows]
    assert db is not None
    stmt = select(Site).order_by(Site.name)
    stmt = ctx.apply_org(stmt, Site.organization_id)
    stmt = ctx.apply_site(stmt, Site.id)
    if organization_id:
        if not ctx.is_super:
            ctx.ensure_org(organization_id)
        stmt = stmt.where(Site.organization_id == organization_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [SiteOut.model_validate(r) for r in rows]


@router.post("", response_model=SiteOut, status_code=201)
async def create_site(
    body: SiteCreate,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.SITE_WRITE)),
) -> SiteOut:
    org_id = body.organization_id or ctx.organization_id
    if not org_id:
        raise ValidationAppError("organization_id is required")
    if is_firestore():
        site = await fs.create_site(
            ctx,
            organization_id=org_id,
            name=body.name,
            address=body.address,
            timezone=body.timezone or "Asia/Kolkata",
            settings=body.settings or {**DEFAULT_SITE_SETTINGS},
        )
        await write_audit(
            db,
            action=AuditAction.SITE_CREATE,
            user_id=ctx.user.id,
            organization_id=org_id,
            ip=client_ip(request),
            target_type="site",
            target_id=site.id,
        )
        return SiteOut.model_validate(site)

    assert db is not None
    if not ctx.is_super:
        ctx.ensure_org(org_id)
    site = Site(
        organization_id=org_id,
        name=body.name,
        address=body.address,
        timezone=body.timezone or "Asia/Kolkata",
        settings=body.settings or {**DEFAULT_SITE_SETTINGS},
    )
    db.add(site)
    await db.flush()
    await write_audit(
        db,
        action=AuditAction.SITE_CREATE,
        user_id=ctx.user.id,
        organization_id=org_id,
        ip=client_ip(request),
        target_type="site",
        target_id=site.id,
    )
    await db.commit()
    await db.refresh(site)
    return SiteOut.model_validate(site)


@router.patch("/{site_id}", response_model=SiteOut)
async def update_site(
    site_id: str,
    body: SiteUpdate,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.SITE_WRITE)),
) -> SiteOut:
    if is_firestore():
        site = await fs.update_site(ctx, site_id, body.model_dump(exclude_unset=True))
        await write_audit(
            db,
            action=AuditAction.SITE_UPDATE,
            user_id=ctx.user.id,
            organization_id=site.organization_id,
            ip=client_ip(request),
            target_type="site",
            target_id=site.id,
        )
        return SiteOut.model_validate(site)

    assert db is not None
    site = await db.get(Site, site_id)
    if not site:
        raise NotFoundError("Site not found")
    ctx.ensure_org(site.organization_id)
    ctx.ensure_site(site.id)
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(site, key, value)
    await write_audit(
        db,
        action=AuditAction.SITE_UPDATE,
        user_id=ctx.user.id,
        organization_id=site.organization_id,
        ip=client_ip(request),
        target_type="site",
        target_id=site.id,
    )
    await db.commit()
    await db.refresh(site)
    return SiteOut.model_validate(site)


@router.get("/{site_id}/connectivity", response_model=SiteConnectivityOut)
async def get_site_connectivity(
    site_id: str,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.GATEWAY_READ)),
) -> SiteConnectivityOut:
    return await site_connectivity(db, ctx, site_id)


@router.patch("/{site_id}/connectivity", response_model=SiteConnectivityOut)
async def patch_site_connectivity(
    site_id: str,
    body: SiteConnectivityUpdate,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.SITE_WRITE)),
) -> SiteConnectivityOut:
    out = await update_site_connectivity(db, ctx, site_id, body)
    await write_audit(
        db,
        action=AuditAction.SITE_CONNECTIVITY_UPDATE,
        user_id=ctx.user.id,
        organization_id=out.organization_id,
        ip=client_ip(request),
        target_type="site",
        target_id=site_id,
        extra={
            "connectivity_mode": out.connectivity_mode,
            "anpr_deployment_mode": out.anpr_deployment_mode,
            "primary_gateway_id": out.primary_gateway_id,
        },
    )
    if db is not None:
        await db.commit()
    return out
