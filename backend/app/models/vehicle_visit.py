from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import VisitStatus

if TYPE_CHECKING:
    from app.models.anpr_event import AnprEvent
    from app.models.vehicle import Vehicle


class VehicleVisit(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "vehicle_visits"

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), nullable=False, index=True)
    vehicle_id: Mapped[str] = mapped_column(ForeignKey("vehicles.id"), nullable=False, index=True)
    plate_normalized: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entry_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    exit_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    entry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gate_id: Mapped[str | None] = mapped_column(ForeignKey("gates.id"), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[VisitStatus] = mapped_column(String(32), nullable=False, index=True)

    vehicle: Mapped[Vehicle] = relationship(back_populates="visits")
    events: Mapped[list[AnprEvent]] = relationship(
        back_populates="visit",
        foreign_keys="AnprEvent.visit_id",
    )
