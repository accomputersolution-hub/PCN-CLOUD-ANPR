from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.gateway import Gateway
    from app.models.organization import Organization
    from app.models.site import Site


class Nvr(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """On-site NVR. Cameras may be NVR channels rather than standalone IPs."""

    __tablename__ = "nvrs"

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), nullable=False, index=True)
    gateway_id: Mapped[str | None] = mapped_column(ForeignKey("gateways.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    vendor: Mapped[str] = mapped_column(String(80), default="GENERIC", nullable=False)
    model: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    host: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    channel_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str] = mapped_column(String(500), default="", nullable=False)

    organization: Mapped[Organization] = relationship()
    site: Mapped[Site] = relationship(back_populates="nvrs")
    gateway: Mapped[Gateway | None] = relationship(back_populates="nvrs")
    cameras: Mapped[list[Camera]] = relationship(back_populates="nvr")
