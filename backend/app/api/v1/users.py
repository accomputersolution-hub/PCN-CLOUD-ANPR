from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_db, get_tenant, require_permission
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.core.rbac import Permission
from app.core.runtime import is_firestore
from app.core.security import hash_password
from app.identity.principal import Principal
from app.models.enums import AuditAction, UserRole
from app.models.site import UserSiteAccess
from app.models.user import User
from app.schemas.auth import UserCreate, UserPublic, UserUpdate
from app.services import firestore_domain as fs
from app.services.audit import write_audit
from app.services.auth import to_public
from app.services.tenant import TenantContext

router = APIRouter(prefix="/users", tags=["users"])


def _can_manage(actor: Principal | User, target_role: UserRole, org_id: str | None) -> None:
    role = actor.role_enum if isinstance(actor, Principal) else (
        actor.role if isinstance(actor.role, UserRole) else UserRole(str(actor.role))
    )
    actor_org = actor.organization_id
    if role == UserRole.SUPER_ADMIN:
        return
    if role != UserRole.ORG_ADMIN:
        raise ForbiddenError()
    if target_role == UserRole.SUPER_ADMIN:
        raise ForbiddenError("Cannot assign Super Admin")
    if org_id != actor_org:
        raise ForbiddenError()


@router.get("", response_model=list[UserPublic])
async def list_users(
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.USER_READ)),
) -> list[UserPublic]:
    if is_firestore():
        rows = await fs.list_users(ctx)
        return [
            UserPublic.model_validate(
                {
                    "id": r.id,
                    "email": r.email,
                    "full_name": r.full_name,
                    "role": r.role,
                    "organization_id": r.organization_id,
                    "is_active": r.is_active,
                    "last_login_at": r.last_login_at,
                    "site_ids": r.site_ids,
                    "auth_provider": r.auth_provider,
                }
            )
            for r in rows
        ]
    assert db is not None
    stmt = select(User).order_by(User.full_name)
    if not ctx.is_super:
        stmt = stmt.where(User.organization_id == ctx.organization_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [UserPublic.model_validate(await to_public(db, u)) for u in rows]


@router.post("", response_model=UserPublic, status_code=201)
async def create_user(
    body: UserCreate,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.USER_WRITE)),
) -> UserPublic:
    org_id = body.organization_id if ctx.is_super else ctx.organization_id
    _can_manage(ctx.user, body.role, org_id)
    if body.role != UserRole.SUPER_ADMIN and not org_id:
        raise ValidationAppError("organization_id is required for this role")

    if is_firestore():
        profile = await fs.create_user_profile(
            ctx,
            email=body.email,
            full_name=body.full_name,
            role=str(body.role),
            organization_id=None if body.role == UserRole.SUPER_ADMIN else org_id,
            site_ids=body.site_ids,
        )
        await write_audit(
            db,
            action=AuditAction.USER_CREATE,
            user_id=ctx.user.id,
            organization_id=profile.organization_id,
            ip=client_ip(request),
            target_type="user",
            target_id=profile.id,
        )
        return UserPublic.model_validate(
            {
                "id": profile.id,
                "email": profile.email,
                "full_name": profile.full_name,
                "role": profile.role,
                "organization_id": profile.organization_id,
                "is_active": profile.is_active,
                "last_login_at": profile.last_login_at,
                "site_ids": profile.site_ids,
                "auth_provider": profile.auth_provider,
            }
        )

    assert db is not None
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
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.USER_WRITE)),
) -> UserPublic:
    if is_firestore():
        from app.repositories import user_profile_repo

        profile = await user_profile_repo().get(user_id)
        if not profile:
            raise NotFoundError("User not found")
        if not ctx.is_super:
            ctx.ensure_org(profile.organization_id or "")
        data = body.model_dump(exclude_unset=True)
        site_ids = data.pop("site_ids", None)
        data.pop("password", None)  # Firebase Auth manages passwords
        if "role" in data:
            _can_manage(ctx.user, data["role"], profile.organization_id)
            data["role"] = str(data["role"])
        for key, value in data.items():
            setattr(profile, key, value)
        if site_ids is not None:
            profile.site_ids = list(site_ids)
        await user_profile_repo().save(profile)
        await write_audit(
            db,
            action=AuditAction.USER_UPDATE,
            user_id=ctx.user.id,
            organization_id=profile.organization_id,
            ip=client_ip(request),
            target_type="user",
            target_id=profile.id,
        )
        return UserPublic.model_validate(
            {
                "id": profile.id,
                "email": profile.email,
                "full_name": profile.full_name,
                "role": profile.role,
                "organization_id": profile.organization_id,
                "is_active": profile.is_active,
                "last_login_at": profile.last_login_at,
                "site_ids": profile.site_ids,
                "auth_provider": profile.auth_provider,
            }
        )

    assert db is not None
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
