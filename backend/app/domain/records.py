"""Datastore-agnostic domain records (Firestore source of truth).

Image bytes are never stored in these records — only storage object keys.
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
    """App profile keyed by Firebase Auth uid (document id = uid)."""

    id: str
    organization_id: str | None = None
    email: str
    full_name: str = ""
    role: str
    is_active: bool = True
    site_ids: list[str] = Field(default_factory=list)
    auth_provider: str = "firebase"
    firebase_uid: str | None = None
    last_login_at: datetime | None = None


class GateRecord(BaseModel):
    id: str
    organization_id: str
    site_id: str
    name: str
    mode: str = "MIXED"
    is_active: bool = True


class AnprRoiRecord(BaseModel):
    """Normalized rectangular ANPR zone (0..1 relative to frame). None/disabled = full frame."""

    enabled: bool = True
    x: float = 0.0
    y: float = 0.0
    w: float = 1.0
    h: float = 1.0


class AnprCalibrationSummary(BaseModel):
    """Compact installer calibration result (no image bytes)."""

    status: str = "RED"
    overall_score: float = 0.0
    component_scores: dict[str, Any] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    guidance: list[str] = Field(default_factory=list)
    plate_width_px: float | None = None
    plate_height_px: float | None = None
    plate_text: str | None = None
    ocr_confidence: float | None = None
    brightness: float | None = None
    sharpness: float | None = None
    roi_enabled: bool = False
    vehicle_in_roi: bool | None = None
    processing_ms: int = 0


class AnprCalibrationRecord(BaseModel):
    """Latest + previous calibration summaries only (no image history)."""

    updated_at: str
    latest: AnprCalibrationSummary
    previous: AnprCalibrationSummary | None = None


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
    streaming: bool = False
    resolution: str = "1920x1080"
    onvif_ip: str | None = None
    last_seen: datetime | None = None
    last_heartbeat: datetime | None = None
    last_frame_at: datetime | None = None
    fps: float | None = None
    connection_error: str | None = None
    retry_count: int = 0
    has_credentials: bool = False
    # Optional gate ANPR zone (normalized rect). Null = ROI disabled (legacy behavior).
    anpr_roi: AnprRoiRecord | None = None
    # Installer calibration latest+previous only (no JPEG history).
    anpr_calibration: AnprCalibrationRecord | None = None
    # Server-only Fernet ciphertext (never returned to clients; rules deny client writes).
    rtsp_url_encrypted: str | None = None
    username_encrypted: str | None = None
    password_encrypted: str | None = None


class VehicleRecord(BaseModel):
    id: str
    organization_id: str
    plate_normalized: str
    first_seen: datetime
    last_seen: datetime
    total_visits: int = 0
    currently_inside: bool = False
    visitor_note: str | None = None
    classification: str | None = None


class SiteVehicleRegistrationRecord(BaseModel):
    """Site-scoped vehicle registry entry (Feature 3)."""

    id: str
    organization_id: str
    site_id: str
    plate_normalized: str
    vehicle_id: str | None = None
    category: str = "resident"
    person_name: str = ""
    mobile_number: str | None = None
    flat_room_unit: str | None = None
    notes: str | None = None
    active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


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
    snapshot_storage_key: str | None = None
    plate_crop_storage_key: str | None = None
    vehicle_crop_storage_key: str | None = None
    processing_duration_ms: int = 0
    source_type: str = "EDGE"
    sync_status: str = "SYNCED"
    classification: str | None = None
    notes: str | None = None
    operator_user_id: str | None = None


class EdgeAgentRecord(BaseModel):
    id: str
    organization_id: str
    site_id: str
    name: str = ""
    agent_key_hash: str
    status: str = "OFFLINE"
    last_seen: datetime | None = None
    last_sync_at: datetime | None = None
    cpu_usage: float | None = None
    memory_usage: float | None = None
    queue_size: int = 0
    is_active: bool = True


class VisitRecord(BaseModel):
    id: str
    organization_id: str
    site_id: str
    vehicle_id: str
    plate_normalized: str
    entry_event_id: str | None = None
    exit_event_id: str | None = None
    entry_at: datetime | None = None
    exit_at: datetime | None = None
    gate_id: str | None = None
    duration_seconds: int | None = None
    status: str = "OPEN"


class RefreshTokenRecord(BaseModel):
    id: str
    user_id: str
    token_hash: str
    expires_at: datetime
    revoked: bool = False
