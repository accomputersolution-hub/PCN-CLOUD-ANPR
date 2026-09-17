from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    GatewayDeviceType,
    GatewayHealthStatus,
    GatewayProvisioningStatus,
    VpnStatus,
)

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.nvr import Nvr
    from app.models.organization import Organization
    from app.models.site import Site


class Gateway(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Site connectivity device: existing VPN router or PCN Cloud Gateway.

    Vendor/model are free-form strings so MikroTik, TP-Link ER605-class, and
    later providers can be recorded without hard-coding hardware automation.
    """

    __tablename__ = "gateways"

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    device_type: Mapped[GatewayDeviceType] = mapped_column(String(32), nullable=False)
    vendor: Mapped[str] = mapped_column(String(80), default="GENERIC", nullable=False)
    model: Mapped[str] = mapped_column(String(80), default="", nullable=False)
    firmware_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vpn_status: Mapped[VpnStatus] = mapped_column(String(24), default=VpnStatus.UNKNOWN, nullable=False)
    health_status: Mapped[GatewayHealthStatus] = mapped_column(
        String(24), default=GatewayHealthStatus.UNKNOWN, nullable=False
    )
    provisioning_status: Mapped[GatewayProvisioningStatus] = mapped_column(
        String(24), default=GatewayProvisioningStatus.UNPROVISIONED, nullable=False
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lan_subnet: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    device_key_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    key_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    config_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    cpu_usage: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_usage: Mapped[float | None] = mapped_column(Float, nullable=True)

    organization: Mapped[Organization] = relationship()
    site: Mapped[Site] = relationship(back_populates="gateways")
    nvrs: Mapped[list[Nvr]] = relationship(back_populates="gateway")
    cameras: Mapped[list[Camera]] = relationship(back_populates="gateway")
