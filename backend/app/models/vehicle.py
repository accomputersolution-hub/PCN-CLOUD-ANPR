from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.anpr_event import AnprEvent
    from app.models.vehicle_visit import VehicleVisit


class Vehicle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "vehicles"
    __table_args__ = (
        UniqueConstraint("organization_id", "plate_normalized", name="uq_vehicles_org_plate"),
    )

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    plate_normalized: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_visits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currently_inside: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    visitor_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    classification: Mapped[str | None] = mapped_column(String(64), nullable=True)

    visits: Mapped[list[VehicleVisit]] = relationship(back_populates="vehicle")
    events: Mapped[list[AnprEvent]] = relationship(back_populates="vehicle")
