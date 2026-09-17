"""Runtime identity principal (datastore-agnostic).

Used by TenantContext / RBAC. Backed by Firestore ``users`` docs when
DATASTORE_PROVIDER=firestore, or by the legacy SQLAlchemy User model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.models.enums import UserRole


@dataclass
class Principal:
    id: str
    email: str
    full_name: str
    role: UserRole | str
    organization_id: str | None
    is_active: bool = True
    site_ids: list[str] = field(default_factory=list)
    auth_provider: str = "firebase"
    firebase_uid: str | None = None
    last_login_at: datetime | None = None

    @property
    def role_enum(self) -> UserRole:
        if isinstance(self.role, UserRole):
            return self.role
        return UserRole(str(self.role))

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "email": self.email,
            "full_name": self.full_name,
            "role": self.role_enum,
            "organization_id": self.organization_id,
            "is_active": self.is_active,
            "last_login_at": self.last_login_at,
            "site_ids": list(self.site_ids),
            "auth_provider": self.auth_provider,
        }


def principal_from_profile(profile: Any) -> Principal:
    """Build Principal from UserProfileRecord or ORM User-like object."""
    role = getattr(profile, "role", UserRole.VIEWER)
    site_ids = list(getattr(profile, "site_ids", None) or [])
    return Principal(
        id=str(profile.id),
        email=str(profile.email).lower(),
        full_name=str(getattr(profile, "full_name", "") or ""),
        role=role,
        organization_id=getattr(profile, "organization_id", None),
        is_active=bool(getattr(profile, "is_active", True)),
        site_ids=site_ids,
        auth_provider=str(getattr(profile, "auth_provider", "firebase")),
        firebase_uid=getattr(profile, "firebase_uid", None),
        last_login_at=getattr(profile, "last_login_at", None),
    )
