from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.site import Site
    from app.models.user import User


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (UniqueConstraint("slug", name="uq_organizations_slug"),)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    sites: Mapped[list[Site]] = relationship(back_populates="organization")
    users: Mapped[list[User]] = relationship(back_populates="organization")
