from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_current_user, get_db
from app.core.config import get_settings
from app.core.rbac import Permission, has_permission
from app.models.enums import AuditAction
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, RefreshRequest, TokenResponse, UserPublic
from app.schemas.common import MessageResponse
from app.services.audit import write_audit
from app.services.auth import authenticate, issue_tokens, revoke_refresh, rotate_refresh, to_public

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)) -> LoginResponse:
    user = await authenticate(db, body.email, body.password)
    access, refresh, expires_in = await issue_tokens(db, user)
    await write_audit(
        db,
        action=AuditAction.LOGIN,
        user_id=user.id,
        organization_id=user.organization_id,
        ip=client_ip(request),
        target_type="user",
        target_id=user.id,
    )
    await db.commit()
    return LoginResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=expires_in,
        user=UserPublic.model_validate(await to_public(db, user)),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    _user, access, refresh_token, expires_in = await rotate_refresh(db, body.refresh_token)
    await db.commit()
    return TokenResponse(access_token=access, refresh_token=refresh_token, expires_in=expires_in)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    body: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MessageResponse:
    await revoke_refresh(db, body.refresh_token)
    await write_audit(
        db,
        action=AuditAction.LOGOUT,
        user_id=user.id,
        organization_id=user.organization_id,
        ip=client_ip(request),
        target_type="user",
        target_id=user.id,
    )
    await db.commit()
    return MessageResponse(message="Logged out")


@router.get("/me", response_model=UserPublic)
async def me(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> UserPublic:
    return UserPublic.model_validate(await to_public(db, user))


@router.get("/permissions")
async def permissions(user: User = Depends(get_current_user)) -> dict:
    perms = [p.value for p in Permission if has_permission(user.role, p)]
    return {"role": user.role, "permissions": perms, "access_minutes": get_settings().access_token_expire_minutes}
