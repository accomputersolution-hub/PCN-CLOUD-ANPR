from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import VehicleRegistryCategory

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.site import Site
    from app.models.vehicle import Vehicle


class SiteVehicleRegistration(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Site-scoped resident/guest/staff/vendor registry (Feature 3).

    Operational visit history remains on org-scoped ``vehicles``.
    """

    __tablename__ = "site_vehicle_registrations"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "site_id",
            "plate_normalized",
            name="uq_site_vehicle_reg_org_site_plate",
        ),
    )

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), nullable=False, index=True)
    plate_normalized: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    vehicle_id: Mapped[str | None] = mapped_column(ForeignKey("vehicles.id"), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(16), nullable=False, default=VehicleRegistryCategory.RESIDENT)
    person_name: Mapped[str] = mapped_column(String(200), nullable=False)
    mobile_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    flat_room_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    organization: Mapped[Organization] = relationship()
    site: Mapped[Site] = relationship()
    vehicle: Mapped[Vehicle | None] = relationship()
