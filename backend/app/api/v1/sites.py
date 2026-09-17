from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_db, get_tenant, require_permission
from app.core.exceptions import NotFoundError
from app.core.rbac import Permission
from app.models.enums import AuditAction
from app.models.site import DEFAULT_SITE_SETTINGS, Site
from app.schemas.site import SiteCreate, SiteOut, SiteUpdate
from app.services.audit import write_audit
from app.services.tenant import TenantContext

router = APIRouter(prefix="/sites", tags=["sites"])


@router.get("", response_model=list[SiteOut])
async def list_sites(
    organization_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.SITE_READ)),
) -> list[SiteOut]:
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
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.SITE_WRITE)),
) -> SiteOut:
    org_id = body.organization_id or ctx.organization_id
    if not org_id:
        from app.core.exceptions import ValidationAppError

        raise ValidationAppError("organization_id is required")
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
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.SITE_WRITE)),
) -> SiteOut:
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
