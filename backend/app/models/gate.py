from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import GateMode

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.site import Site


class Gate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "gates"

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    mode: Mapped[GateMode] = mapped_column(String(16), default=GateMode.MIXED, nullable=False)

    site: Mapped[Site] = relationship(back_populates="gates")
    cameras: Mapped[list[Camera]] = relationship(back_populates="gate")
