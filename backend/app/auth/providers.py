"""Authentication provider abstraction.

AuthProvider
  ├── JwtAuthProvider       (default — PostgreSQL users + JWT)
  └── FirebaseAuthProvider  (optional — Firebase Auth email/password + Firestore profiles)
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError, ValidationAppError
from app.core.providers import normalize_auth_provider, require_firebase_admin_for
from app.firebase.admin import get_firebase_admin_app
from app.identity.principal import Principal, principal_from_profile
from app.repositories import user_profile_repo
from app.services import auth_firestore
from app.services.auth import authenticate, issue_tokens, revoke_refresh, rotate_refresh, to_public


class AuthProvider(Protocol):
    name: str

    async def login(
        self,
        db: AsyncSession | None,
        email: str,
        password: str,
    ) -> tuple[Principal, str, str, int]:
        """Return principal, access_token, refresh_token, expires_in."""

    async def refresh(
        self,
        db: AsyncSession | None,
        refresh_token: str,
    ) -> tuple[Principal, str, str, int]:
        ...

    async def logout(self, db: AsyncSession | None, refresh_token: str) -> None:
        ...

    async def public_user(self, db: AsyncSession | None, principal: Principal) -> dict[str, Any]:
        ...


class JwtAuthProvider:
    """Current production auth: bcrypt password hash + JWT access/refresh."""

    name = "jwt"

    async def login(
        self,
        db: AsyncSession | None,
        email: str,
        password: str,
    ) -> tuple[Principal, str, str, int]:
        if db is None:
            raise ValidationAppError("JWT auth requires a SQLAlchemy database session")
        user = await authenticate(db, email, password)
        access, refresh, expires_in = await issue_tokens(db, user)
        return principal_from_profile(user), access, refresh, expires_in

    async def refresh(
        self,
        db: AsyncSession | None,
        refresh_token: str,
    ) -> tuple[Principal, str, str, int]:
        if db is None:
            raise ValidationAppError("JWT auth requires a SQLAlchemy database session")
        user, access, refresh, expires_in = await rotate_refresh(db, refresh_token)
        return principal_from_profile(user), access, refresh, expires_in

    async def logout(self, db: AsyncSession | None, refresh_token: str) -> None:
        if db is None:
            raise ValidationAppError("JWT auth requires a SQLAlchemy database session")
        await revoke_refresh(db, refresh_token)

    async def public_user(self, db: AsyncSession | None, principal: Principal) -> dict[str, Any]:
        if db is None:
            return auth_firestore.public_principal(principal)
        # Enrich site_ids from UserSiteAccess when principal was built from ORM User.
        from app.models.user import User

        user = await db.get(User, principal.id)
        if user is None:
            return auth_firestore.public_principal(principal)
        profile = await to_public(db, user)
        profile["auth_provider"] = self.name
        return profile


class FirebaseAuthProvider:
    """Firebase Authentication (email/password) with Firestore RBAC profiles.

    Flow when AUTH_PROVIDER=firebase:
    1. Sign in against Firebase Identity Toolkit (email/password).
    2. Verify the ID token with Admin SDK (no fake success).
    3. Resolve the organization user profile in Firestore by Firebase uid or email.
    4. Issue API JWT pair stored in Firestore refreshTokens.

    Does not invent users or bypass org isolation.
    """

    name = "firebase"

    def __init__(self) -> None:
        require_firebase_admin_for("Firebase Authentication")
        settings = get_settings()
        if not (settings.firebase_api_key or "").strip():
            raise ValidationAppError(
                "FIREBASE_API_KEY is required when AUTH_PROVIDER=firebase "
                "(web API key from the Firebase console; not an Admin private key).",
            )

    def verify_id_token(self, id_token: str) -> dict[str, Any]:
        require_firebase_admin_for("Firebase ID token verification")
        app = get_firebase_admin_app()
        try:
            from firebase_admin import auth as fb_auth
        except ImportError as exc:  # pragma: no cover
            raise ValidationAppError(
                "firebase-admin is not installed. pip install -r requirements-firebase.txt",
            ) from exc
        try:
            # Allow small client/server clock skew (common on Windows/dev laptops).
            return fb_auth.verify_id_token(id_token, app=app, clock_skew_seconds=10)
        except TypeError:
            # Older firebase-admin without clock_skew_seconds
            return fb_auth.verify_id_token(id_token, app=app)
        except Exception as exc:  # noqa: BLE001
            from app.core.logging import get_logger

            get_logger(__name__).warning("firebase.id_token_verify_failed", error=str(exc))
            raise UnauthorizedError("Invalid Firebase ID token") from exc

    async def login(
        self,
        db: AsyncSession | None,
        email: str,
        password: str,
    ) -> tuple[Principal, str, str, int]:
        settings = get_settings()
        api_key = settings.firebase_api_key.strip()
        url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.post(
                url,
                json={"email": email, "password": password, "returnSecureToken": True},
            )
        if res.status_code >= 400:
            raise UnauthorizedError("Invalid email or password")
        body = res.json()
        id_token = body.get("idToken")
        if not id_token:
            raise UnauthorizedError("Firebase authentication failed")
        claims = self.verify_id_token(id_token)
        fb_uid = claims.get("uid") or claims.get("user_id")
        fb_email = (claims.get("email") or email).lower()

        repo = user_profile_repo()
        profile = None
        if fb_uid:
            profile = await repo.get(str(fb_uid))
        if profile is None:
            profile = await repo.get_by_email(fb_email)
        if profile is None or not profile.is_active:
            raise UnauthorizedError(
                "Firebase user is authenticated but has no active organization profile. "
                "Create a Firestore users document (id = Firebase uid) with matching email first.",
            )

        # Prefer firebase uid on the principal when the doc id already is the uid;
        # if found by email under a different id, still use that profile as-is.
        if not profile.firebase_uid and fb_uid:
            profile.firebase_uid = str(fb_uid)
        profile.last_login_at = datetime.now(UTC)
        profile.auth_provider = self.name
        await repo.save(profile)

        principal = principal_from_profile(profile)
        access, refresh, expires_in = await auth_firestore.issue_tokens_fs(principal)
        return principal, access, refresh, expires_in

    async def refresh(
        self,
        db: AsyncSession | None,
        refresh_token: str,
    ) -> tuple[Principal, str, str, int]:
        return await auth_firestore.rotate_refresh_fs(refresh_token)

    async def logout(self, db: AsyncSession | None, refresh_token: str) -> None:
        await auth_firestore.revoke_refresh_fs(refresh_token)

    async def public_user(self, db: AsyncSession | None, principal: Principal) -> dict[str, Any]:
        profile = auth_firestore.public_principal(principal)
        profile["auth_provider"] = self.name
        return profile


def get_auth_provider() -> AuthProvider:
    provider = normalize_auth_provider(get_settings().auth_provider)
    if provider == "jwt":
        return JwtAuthProvider()
    if provider == "firebase":
        return FirebaseAuthProvider()
    raise ValidationAppError(f"Unknown AUTH_PROVIDER: {provider}")
