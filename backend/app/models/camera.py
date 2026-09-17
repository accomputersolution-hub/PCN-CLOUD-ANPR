from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import CameraStatus, Direction, StreamType

if TYPE_CHECKING:
    from app.models.gate import Gate
    from app.models.organization import Organization
    from app.models.site import Site


class Camera(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "cameras"

    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), nullable=False, index=True)
    gate_id: Mapped[str] = mapped_column(ForeignKey("gates.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    camera_code: Mapped[str] = mapped_column(String(64), nullable=False)
    direction: Mapped[Direction] = mapped_column(String(16), nullable=False)
    rtsp_url_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    onvif_ip: Mapped[str | None] = mapped_column(String(128), nullable=True)
    username_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    stream_type: Mapped[StreamType] = mapped_column(String(16), default=StreamType.RTSP, nullable=False)
    resolution: Mapped[str] = mapped_column(String(32), default="1920x1080", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[CameraStatus] = mapped_column(String(16), default=CameraStatus.UNKNOWN, nullable=False)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fps: Mapped[float | None] = mapped_column(Float, nullable=True)
    connection_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    last_frame_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    streaming: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    organization: Mapped[Organization] = relationship()
    site: Mapped[Site] = relationship(back_populates="cameras")
    gate: Mapped[Gate] = relationship(back_populates="cameras")
