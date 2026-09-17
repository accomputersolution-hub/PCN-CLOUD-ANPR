from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import Direction, SourceType, SyncStatus

if TYPE_CHECKING:
    from app.models.camera import Camera
    from app.models.gate import Gate
    from app.models.snapshot import Snapshot
    from app.models.vehicle import Vehicle
    from app.models.vehicle_visit import VehicleVisit


class AnprEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "anpr_events"

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), nullable=False, index=True)
    gate_id: Mapped[str] = mapped_column(ForeignKey("gates.id"), nullable=False, index=True)
    camera_id: Mapped[str] = mapped_column(ForeignKey("cameras.id"), nullable=False, index=True)
    vehicle_id: Mapped[str | None] = mapped_column(ForeignKey("vehicles.id"), nullable=True, index=True)
    visit_id: Mapped[str | None] = mapped_column(
        ForeignKey("vehicle_visits.id", use_alter=True, name="fk_anpr_events_visit_id"),
        nullable=True,
        index=True,
    )
    direction: Mapped[Direction] = mapped_column(String(16), nullable=False)
    plate_text: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_ocr_text: Mapped[str] = mapped_column(String(64), nullable=False)
    plate_normalized: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    ocr_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    plate_detection_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    vehicle_detection_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    local_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    plate_crop_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    vehicle_crop_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    processing_duration_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    source_type: Mapped[SourceType] = mapped_column(String(16), default=SourceType.EDGE, nullable=False)
    sync_status: Mapped[SyncStatus] = mapped_column(String(16), default=SyncStatus.SYNCED, nullable=False)
    classification: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    vehicle: Mapped[Vehicle | None] = relationship(back_populates="events")
    visit: Mapped[VehicleVisit | None] = relationship(back_populates="events", foreign_keys=[visit_id])
    camera: Mapped[Camera] = relationship()
    gate: Mapped[Gate] = relationship()
    snapshots: Mapped[list[Snapshot]] = relationship(back_populates="event")
