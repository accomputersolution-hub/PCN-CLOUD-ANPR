from __future__ import annotations

from hashlib import sha256
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.timeutil import ensure_utc
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.services.tenant import load_site_ids
from datetime import UTC, datetime, timedelta


def _token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


async def authenticate(db: AsyncSession, email: str, password: str) -> User:
    user = (await db.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(password, user.hashed_password):
        raise UnauthorizedError("Invalid email or password")
    user.last_login_at = datetime.now(UTC)
    return user


async def issue_tokens(db: AsyncSession, user: User) -> tuple[str, str, int]:
    access = create_access_token(UUID(user.id))
    refresh = create_refresh_token(UUID(user.id))
    days = get_settings().refresh_token_expire_days
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=_token_hash(refresh),
            expires_at=datetime.now(UTC) + timedelta(days=days),
            revoked=False,
        )
    )
    expires_in = get_settings().access_token_expire_minutes * 60
    return access, refresh, expires_in


async def rotate_refresh(db: AsyncSession, refresh_token: str) -> tuple[User, str, str, int]:
    try:
        payload = decode_token(refresh_token, "refresh")
    except ValueError as exc:
        raise UnauthorizedError(str(exc)) from exc
    stored = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == _token_hash(refresh_token)))
    ).scalar_one_or_none()
    # SQLite may return naive expires_at; treat as UTC so expiry checks stay correct.
    if stored is None or stored.revoked or ensure_utc(stored.expires_at) < datetime.now(UTC):
        raise UnauthorizedError("Refresh token is invalid")
    stored.revoked = True
    user = await db.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise UnauthorizedError("User is inactive")
    access, refresh, expires_in = await issue_tokens(db, user)
    return user, access, refresh, expires_in


async def revoke_refresh(db: AsyncSession, refresh_token: str) -> None:
    stored = (
        await db.execute(select(RefreshToken).where(RefreshToken.token_hash == _token_hash(refresh_token)))
    ).scalar_one_or_none()
    if stored:
        stored.revoked = True


async def to_public(db: AsyncSession, user: User) -> dict:
    site_ids = await load_site_ids(db, user)
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "organization_id": user.organization_id,
        "is_active": user.is_active,
        "last_login_at": user.last_login_at,
        "site_ids": site_ids,
    }


def new_user_id() -> str:
    return str(uuid4())


__all__ = ["authenticate", "issue_tokens", "rotate_refresh", "revoke_refresh", "to_public", "hash_password"]
