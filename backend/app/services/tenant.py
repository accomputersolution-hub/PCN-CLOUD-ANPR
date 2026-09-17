from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.core.exceptions import ForbiddenError
from app.models.enums import UserRole
from app.models.user import User


class TenantContext:
    def __init__(self, user: User, site_ids: list[str]) -> None:
        self.user = user
        self.site_ids = site_ids

    @property
    def is_super(self) -> bool:
        return self.user.role == UserRole.SUPER_ADMIN

    @property
    def organization_id(self) -> str | None:
        return self.user.organization_id

    def ensure_org(self, organization_id: str) -> None:
        if self.is_super:
            return
        if self.organization_id != organization_id:
            raise ForbiddenError("Tenant isolation violation")

    def ensure_site(self, site_id: str) -> None:
        if self.is_super:
            return
        if self.site_ids and site_id not in self.site_ids:
            raise ForbiddenError("Site access denied")

    def org_filter(self, column: ColumnElement[str]) -> ColumnElement[bool] | None:
        if self.is_super:
            return None
        if not self.organization_id:
            raise ForbiddenError("User is not assigned to an organization")
        return column == self.organization_id

    def apply_org(self, stmt: Select, column: ColumnElement[str]) -> Select:
        clause = self.org_filter(column)
        if clause is not None:
            stmt = stmt.where(clause)
        return stmt

    def apply_site(self, stmt: Select, column: ColumnElement[str]) -> Select:
        if self.is_super:
            return stmt
        if self.site_ids:
            stmt = stmt.where(column.in_(self.site_ids))
        return stmt


async def load_site_ids(db: AsyncSession, user: User) -> list[str]:
    from app.models.site import UserSiteAccess

    if user.role in {UserRole.SUPER_ADMIN, UserRole.ORG_ADMIN}:
        return []
    rows = (
        await db.execute(select(UserSiteAccess.site_id).where(UserSiteAccess.user_id == user.id))
    ).scalars().all()
    return list(rows)


def as_uuid(value: str) -> UUID:
    return UUID(value)
