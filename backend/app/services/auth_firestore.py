"""Firestore-backed API JWT issue / rotate / revoke (AUTH_PROVIDER=firebase path)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    create_token,
    decode_token,
)
from app.core.timeutil import ensure_utc
from app.domain.records import RefreshTokenRecord
from app.identity.principal import Principal, principal_from_profile
from app.repositories import refresh_token_repo, user_profile_repo


def _token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def _mint_pair(user_id: str) -> tuple[str, str]:
    """Mint access + refresh JWTs. Firebase UIDs are not always UUID-shaped."""
    settings = get_settings()
    try:
        uid = UUID(user_id)
        return create_access_token(uid), create_refresh_token(uid)
    except ValueError:
        access = create_token(
            user_id,
            "access",
            timedelta(minutes=settings.access_token_expire_minutes),
        )
        refresh = create_token(
            user_id,
            "refresh",
            timedelta(days=settings.refresh_token_expire_days),
        )
        return access, refresh


async def issue_tokens_fs(principal: Principal) -> tuple[str, str, int]:
    access, refresh = _mint_pair(principal.id)
    token_hash = _token_hash(refresh)
    days = get_settings().refresh_token_expire_days
    record = RefreshTokenRecord(
        id=token_hash,
        user_id=principal.id,
        token_hash=token_hash,
        expires_at=datetime.now(UTC) + timedelta(days=days),
        revoked=False,
    )
    await refresh_token_repo().add(record)
    expires_in = get_settings().access_token_expire_minutes * 60
    return access, refresh, expires_in


async def rotate_refresh_fs(refresh_token: str) -> tuple[Principal, str, str, int]:
    try:
        payload = decode_token(refresh_token, "refresh")
    except ValueError as exc:
        raise UnauthorizedError(str(exc)) from exc

    token_hash = _token_hash(refresh_token)
    repo = refresh_token_repo()
    stored = await repo.get_by_hash(token_hash)
    if stored is None or stored.revoked or ensure_utc(stored.expires_at) < datetime.now(UTC):
        raise UnauthorizedError("Refresh token is invalid")

    await repo.revoke(token_hash)

    user_id = str(payload.get("sub") or stored.user_id)
    principal = await load_principal(user_id)
    if not principal.is_active:
        raise UnauthorizedError("User is inactive")

    access, refresh, expires_in = await issue_tokens_fs(principal)
    return principal, access, refresh, expires_in


async def revoke_refresh_fs(refresh_token: str) -> None:
    await refresh_token_repo().revoke(_token_hash(refresh_token))


async def load_principal(user_id: str) -> Principal:
    profile = await user_profile_repo().get(user_id)
    if profile is None:
        raise UnauthorizedError("User not found or inactive")
    if not profile.is_active:
        raise UnauthorizedError("User is inactive")
    return principal_from_profile(profile)


def public_principal(principal: Principal) -> dict:
    return principal.to_public()


__all__ = [
    "issue_tokens_fs",
    "rotate_refresh_fs",
    "revoke_refresh_fs",
    "load_principal",
    "public_principal",
]
