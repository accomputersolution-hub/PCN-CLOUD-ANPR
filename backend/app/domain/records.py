"""Datastore-agnostic domain records for future Firestore documents.

SQLAlchemy remains the live persistence layer. These records define the
fields we intend to store in Firestore (metadata only — no image bytes).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class OrganizationRecord(BaseModel):
    id: str
    name: str
    slug: str
    retention_days: int = 90
    is_active: bool = True


class SiteRecord(BaseModel):
    id: str
    organization_id: str
    name: str
    address: str = ""
    timezone: str = "Asia/Kolkata"
    is_active: bool = True
    connectivity_mode: str = "EXISTING_VPN_ROUTER"
    anpr_deployment_mode: str = "LOCAL_EDGE_AGENT"
    primary_gateway_id: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class UserProfileRecord(BaseModel):
    """App profile linked to AUTH_PROVIDER identity (JWT user id or Firebase uid)."""

    id: str
    organization_id: str | None = None
    email: str
    full_name: str = ""
    role: str
    is_active: bool = True
    site_ids: list[str] = Field(default_factory=list)
    auth_provider: str = "jwt"
    firebase_uid: str | None = None


class CameraRecord(BaseModel):
    id: str
    organization_id: str
    site_id: str
    gate_id: str | None = None
    name: str
    camera_code: str = ""
    direction: str = "ENTRY"
    source_type: str = "RTSP"
    nvr_id: str | None = None
    channel: str | None = None
    gateway_id: str | None = None
    stream_type: str = "RTSP"
    status: str = "UNKNOWN"
    enabled: bool = True
    anpr_enabled: bool = True
    last_seen: datetime | None = None
    # Secrets stay encrypted / out of Firestore client responses.
    has_credentials: bool = False


class VehicleRecord(BaseModel):
    id: str
    organization_id: str
    plate_normalized: str
    first_seen: datetime
    last_seen: datetime
    total_visits: int = 0
    currently_inside: bool = False


class AnprEventRecord(BaseModel):
    """Confirmed ANPR evidence metadata. Image bytes live in object storage only."""

    id: str
    organization_id: str
    site_id: str
    gate_id: str
    camera_id: str
    vehicle_id: str | None = None
    visit_id: str | None = None
    direction: str
    plate_text: str
    plate_normalized: str
    raw_ocr_text: str = ""
    ocr_confidence: float = 0.0
    plate_detection_confidence: float = 0.0
    vehicle_detection_confidence: float = 0.0
    timestamp: datetime
    local_timestamp: datetime
    # Storage object keys (Firebase Storage / local), never embedded image data.
    snapshot_storage_key: str | None = None
    plate_crop_storage_key: str | None = None
    vehicle_crop_storage_key: str | None = None
    processing_duration_ms: int = 0
    source_type: str = "EDGE"
    sync_status: str = "SYNCED"
    classification: str | None = None
    notes: str | None = None
