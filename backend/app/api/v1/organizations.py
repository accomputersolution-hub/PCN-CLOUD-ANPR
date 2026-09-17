from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_db, get_tenant, require_permission
from app.core.exceptions import ConflictError, NotFoundError
from app.core.rbac import Permission
from app.models.enums import AuditAction, UserRole
from app.models.organization import Organization
from app.schemas.organization import OrganizationCreate, OrganizationOut, OrganizationUpdate
from app.services.audit import write_audit
from app.services.tenant import TenantContext

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get("", response_model=list[OrganizationOut])
async def list_orgs(
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.ORG_READ)),
) -> list[OrganizationOut]:
    stmt = select(Organization).order_by(Organization.name)
    if not ctx.is_super:
        stmt = stmt.where(Organization.id == ctx.organization_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [OrganizationOut.model_validate(r) for r in rows]


@router.post("", response_model=OrganizationOut, status_code=201)
async def create_org(
    body: OrganizationCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.PLATFORM_ADMIN)),
) -> OrganizationOut:
    exists = (await db.execute(select(Organization).where(Organization.slug == body.slug))).scalar_one_or_none()
    if exists:
        raise ConflictError("Organization slug already exists")
    org = Organization(name=body.name, slug=body.slug, retention_days=body.retention_days)
    db.add(org)
    await db.flush()
    await write_audit(
        db,
        action=AuditAction.ORGANIZATION_CREATE,
        user_id=ctx.user.id,
        organization_id=org.id,
        ip=client_ip(request),
        target_type="organization",
        target_id=org.id,
    )
    await db.commit()
    await db.refresh(org)
    return OrganizationOut.model_validate(org)


@router.patch("/{org_id}", response_model=OrganizationOut)
async def update_org(
    org_id: str,
    body: OrganizationUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
) -> OrganizationOut:
    org = await db.get(Organization, org_id)
    if not org:
        raise NotFoundError("Organization not found")
    if ctx.user.role == UserRole.SUPER_ADMIN:
        pass
    elif ctx.user.role == UserRole.ORG_ADMIN:
        ctx.ensure_org(org.id)
    else:
        from app.core.exceptions import ForbiddenError

        raise ForbiddenError()
    data = body.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(org, key, value)
    await write_audit(
        db,
        action=AuditAction.ORGANIZATION_UPDATE,
        user_id=ctx.user.id,
        organization_id=org.id,
        ip=client_ip(request),
        target_type="organization",
        target_id=org.id,
    )
    await db.commit()
    await db.refresh(org)
    return OrganizationOut.model_validate(org)
