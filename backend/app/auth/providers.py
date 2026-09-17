"""Authentication provider abstraction.

AuthProvider
  ├── JwtAuthProvider      (current — PostgreSQL users + JWT)
  └── FirebaseAuthProvider (future — fails clearly until configured/wired)
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import ValidationAppError
from app.core.providers import normalize_auth_provider, require_firebase_admin_for
from app.models.user import User
from app.services.auth import authenticate, issue_tokens, revoke_refresh, rotate_refresh, to_public


class AuthProvider(Protocol):
    name: str

    async def login(self, db: AsyncSession, email: str, password: str) -> tuple[User, str, str, int]:
        """Return user, access_token, refresh_token, expires_in."""

    async def refresh(self, db: AsyncSession, refresh_token: str) -> tuple[User, str, str, int]:
        ...

    async def logout(self, db: AsyncSession, refresh_token: str) -> None:
        ...

    async def public_user(self, db: AsyncSession, user: User) -> dict[str, Any]:
        ...


class JwtAuthProvider:
    """Current production auth: bcrypt password hash + JWT access/refresh."""

    name = "jwt"

    async def login(self, db: AsyncSession, email: str, password: str) -> tuple[User, str, str, int]:
        user = await authenticate(db, email, password)
        access, refresh, expires_in = await issue_tokens(db, user)
        return user, access, refresh, expires_in

    async def refresh(self, db: AsyncSession, refresh_token: str) -> tuple[User, str, str, int]:
        return await rotate_refresh(db, refresh_token)

    async def logout(self, db: AsyncSession, refresh_token: str) -> None:
        await revoke_refresh(db, refresh_token)

    async def public_user(self, db: AsyncSession, user: User) -> dict[str, Any]:
        return await to_public(db, user)


class FirebaseAuthProvider:
    """Future Firebase Authentication adapter.

    Does not verify tokens or invent sessions when Firebase is not configured.
    """

    name = "firebase"

    def __init__(self) -> None:
        require_firebase_admin_for("Firebase Authentication")

    async def login(self, db: AsyncSession, email: str, password: str) -> tuple[User, str, str, int]:
        raise ValidationAppError(
            "Firebase Authentication login is not enabled yet. Keep AUTH_PROVIDER=jwt.",
        )

    async def refresh(self, db: AsyncSession, refresh_token: str) -> tuple[User, str, str, int]:
        raise ValidationAppError(
            "Firebase Authentication refresh is not enabled yet. Keep AUTH_PROVIDER=jwt.",
        )

    async def logout(self, db: AsyncSession, refresh_token: str) -> None:
        raise ValidationAppError(
            "Firebase Authentication logout is not enabled yet. Keep AUTH_PROVIDER=jwt.",
        )

    async def public_user(self, db: AsyncSession, user: User) -> dict[str, Any]:
        raise ValidationAppError(
            "Firebase Authentication profiles are not enabled yet. Keep AUTH_PROVIDER=jwt.",
        )

    def verify_id_token(self, id_token: str) -> dict[str, Any]:
        """Reserved for verifying Firebase ID tokens server-side."""
        require_firebase_admin_for("Firebase ID token verification")
        raise ValidationAppError(
            "Firebase ID token verification is not enabled yet. Keep AUTH_PROVIDER=jwt.",
        )


def get_auth_provider() -> AuthProvider:
    provider = normalize_auth_provider(get_settings().auth_provider)
    if provider == "jwt":
        return JwtAuthProvider()
    if provider == "firebase":
        return FirebaseAuthProvider()
    raise ValidationAppError(f"Unknown AUTH_PROVIDER: {provider}")
