from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import SnapshotKind

if TYPE_CHECKING:
    from app.models.anpr_event import AnprEvent


class Snapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "snapshots"

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("anpr_events.id"), nullable=False, index=True)
    kind: Mapped[SnapshotKind] = mapped_column(String(24), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    content_type: Mapped[str] = mapped_column(String(80), default="image/jpeg", nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    event: Mapped[AnprEvent] = relationship(back_populates="snapshots")
