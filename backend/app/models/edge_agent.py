from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import EdgeAgentStatus

if TYPE_CHECKING:
    from app.models.site import Site


class EdgeAgent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "edge_agents"

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    agent_key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[EdgeAgentStatus] = mapped_column(String(24), default=EdgeAgentStatus.UNKNOWN, nullable=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cpu_usage: Mapped[float | None] = mapped_column(Float, nullable=True)
    memory_usage: Mapped[float | None] = mapped_column(Float, nullable=True)
    queue_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[str] = mapped_column(String(32), default="0.1.0", nullable=False)

    site: Mapped[Site] = relationship(back_populates="edge_agents")
