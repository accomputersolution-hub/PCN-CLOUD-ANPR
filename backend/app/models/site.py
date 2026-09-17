from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AnprDeploymentMode, ConnectivityMode

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.edge_agent import EdgeAgent
    from app.models.gate import Gate
    from app.models.gateway import Gateway
    from app.models.nvr import Nvr
    from app.models.organization import Organization
    from app.models.user import User


DEFAULT_SITE_SETTINGS: dict[str, Any] = {
    "min_confidence": 0.70,
    "duplicate_window_seconds": 30,
    "event_cooldown_seconds": 120,
    "confusable_substitution": False,
}


class Site(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "sites"

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    address: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Kolkata", nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    connectivity_mode: Mapped[ConnectivityMode] = mapped_column(
        String(32), default=ConnectivityMode.EXISTING_VPN_ROUTER, nullable=False
    )
    anpr_deployment_mode: Mapped[AnprDeploymentMode] = mapped_column(
        String(32), default=AnprDeploymentMode.LOCAL_EDGE_AGENT, nullable=False
    )
    primary_gateway_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)

    organization: Mapped[Organization] = relationship(back_populates="sites")
    gates: Mapped[list[Gate]] = relationship(back_populates="site")
    cameras: Mapped[list[Camera]] = relationship(back_populates="site")
    edge_agents: Mapped[list[EdgeAgent]] = relationship(back_populates="site")
    gateways: Mapped[list[Gateway]] = relationship(back_populates="site")
    nvrs: Mapped[list[Nvr]] = relationship(back_populates="site")


class UserSiteAccess(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "user_site_access"
    __table_args__ = (UniqueConstraint("user_id", "site_id", name="uq_user_site_access"),)

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), nullable=False, index=True)

    user: Mapped[User] = relationship(back_populates="site_access")
    site: Mapped[Site] = relationship()
