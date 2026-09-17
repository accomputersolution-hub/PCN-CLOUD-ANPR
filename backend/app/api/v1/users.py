from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_db, get_tenant, require_permission
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.core.rbac import Permission
from app.core.security import hash_password
from app.models.enums import AuditAction, UserRole
from app.models.site import UserSiteAccess
from app.models.user import User
from app.schemas.auth import UserCreate, UserPublic, UserUpdate
from app.services.audit import write_audit
from app.services.auth import to_public
from app.services.tenant import TenantContext

router = APIRouter(prefix="/users", tags=["users"])


def _can_manage(actor: User, target_role: UserRole, org_id: str | None) -> None:
    if actor.role == UserRole.SUPER_ADMIN:
        return
    if actor.role != UserRole.ORG_ADMIN:
        raise ForbiddenError()
    if target_role == UserRole.SUPER_ADMIN:
        raise ForbiddenError("Cannot assign Super Admin")
    if org_id != actor.organization_id:
        raise ForbiddenError()


@router.get("", response_model=list[UserPublic])
async def list_users(
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.USER_READ)),
) -> list[UserPublic]:
    stmt = select(User).order_by(User.full_name)
    if not ctx.is_super:
        stmt = stmt.where(User.organization_id == ctx.organization_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [UserPublic.model_validate(await to_public(db, u)) for u in rows]


@router.post("", response_model=UserPublic, status_code=201)
async def create_user(
    body: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.USER_WRITE)),
) -> UserPublic:
    org_id = body.organization_id if ctx.is_super else ctx.organization_id
    _can_manage(ctx.user, body.role, org_id)
    if body.role != UserRole.SUPER_ADMIN and not org_id:
        raise ValidationAppError("organization_id is required for this role")
    exists = (await db.execute(select(User).where(User.email == body.email.lower()))).scalar_one_or_none()
    if exists:
        raise ConflictError("Email already registered")
    user = User(
        email=body.email.lower(),
        hashed_password=hash_password(body.password),
        full_name=body.full_name,
        role=body.role,
        organization_id=None if body.role == UserRole.SUPER_ADMIN else org_id,
    )
    db.add(user)
    await db.flush()
    for site_id in body.site_ids:
        db.add(UserSiteAccess(user_id=user.id, site_id=site_id))
    await write_audit(
        db,
        action=AuditAction.USER_CREATE,
        user_id=ctx.user.id,
        organization_id=user.organization_id,
        ip=client_ip(request),
        target_type="user",
        target_id=user.id,
    )
    await db.commit()
    return UserPublic.model_validate(await to_public(db, user))


@router.patch("/{user_id}", response_model=UserPublic)
async def update_user(
    user_id: str,
    body: UserUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.USER_WRITE)),
) -> UserPublic:
    user = await db.get(User, user_id)
    if not user:
        raise NotFoundError("User not found")
    if not ctx.is_super:
        ctx.ensure_org(user.organization_id or "")
    data = body.model_dump(exclude_unset=True)
    site_ids = data.pop("site_ids", None)
    password = data.pop("password", None)
    if "role" in data:
        _can_manage(ctx.user, data["role"], user.organization_id)
    if password:
        user.hashed_password = hash_password(password)
    for key, value in data.items():
        setattr(user, key, value)
    if site_ids is not None:
        existing = (await db.execute(select(UserSiteAccess).where(UserSiteAccess.user_id == user.id))).scalars().all()
        for row in existing:
            await db.delete(row)
        for site_id in site_ids:
            db.add(UserSiteAccess(user_id=user.id, site_id=site_id))
    await write_audit(
        db,
        action=AuditAction.USER_UPDATE,
        user_id=ctx.user.id,
        organization_id=user.organization_id,
        ip=client_ip(request),
        target_type="user",
        target_id=user.id,
    )
    await db.commit()
    return UserPublic.model_validate(await to_public(db, user))
