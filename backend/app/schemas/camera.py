from datetime import datetime
from typing import Any

from pydantic import Field, model_validator

from app.models.enums import CameraSourceType, CameraStatus, Direction, StreamType
from app.schemas.common import ORMModel


class AnprRoi(ORMModel):
    """Normalized rectangular ANPR zone (0..1). ``enabled=False`` or omit = full frame."""

    enabled: bool = True
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    w: float = Field(gt=0.0, le=1.0)
    h: float = Field(gt=0.0, le=1.0)

    @model_validator(mode="after")
    def _bounds(self) -> "AnprRoi":
        if self.x + self.w > 1.0001 or self.y + self.h > 1.0001:
            raise ValueError("anpr_roi must fit within the unit square")
        return self


class CameraCreate(ORMModel):
    site_id: str
    gate_id: str
    name: str
    camera_code: str
    direction: Direction
    rtsp_url: str | None = None
    onvif_ip: str | None = None
    username: str | None = None
    password: str | None = None
    stream_type: StreamType = StreamType.RTSP
    resolution: str = "1920x1080"
    enabled: bool = True
    source_type: CameraSourceType = CameraSourceType.RTSP
    nvr_id: str | None = None
    channel: str | None = None
    anpr_enabled: bool = True
    gateway_id: str | None = None
    anpr_roi: AnprRoi | None = None


class CameraUpdate(ORMModel):
    name: str | None = None
    camera_code: str | None = None
    gate_id: str | None = None
    direction: Direction | None = None
    rtsp_url: str | None = None
    onvif_ip: str | None = None
    username: str | None = None
    password: str | None = None
    stream_type: StreamType | None = None
    resolution: str | None = None
    enabled: bool | None = None
    source_type: CameraSourceType | None = None
    nvr_id: str | None = None
    channel: str | None = None
    anpr_enabled: bool | None = None
    gateway_id: str | None = None
    # Explicit null clears ROI (ROI disabled).
    anpr_roi: AnprRoi | None = None


class CameraOut(ORMModel):
    id: str
    organization_id: str
    site_id: str
    gate_id: str
    name: str
    camera_code: str
    direction: Direction
    onvif_ip: str | None
    stream_type: StreamType
    resolution: str
    enabled: bool
    streaming: bool = False
    status: CameraStatus
    last_heartbeat: datetime | None
    fps: float | None
    connection_error: str | None
    last_frame_at: datetime | None
    retry_count: int
    credentials_configured: bool = False
    rtsp_configured: bool = False
    site_name: str | None = None
    gate_name: str | None = None
    source_type: CameraSourceType = CameraSourceType.RTSP
    nvr_id: str | None = None
    channel: str | None = None
    anpr_enabled: bool = True
    gateway_id: str | None = None
    last_seen: datetime | None = None
    anpr_roi: AnprRoi | None = None
    anpr_calibration: dict[str, Any] | None = None


class CameraTestRequest(ORMModel):
    rtsp_url: str | None = None
    camera_id: str | None = None


class CameraTestResult(ORMModel):
    ok: bool
    message: str
    probe: str = "url_validation"
    resolution: str | None = None
    fps: float | None = None
    first_frame_received: bool = False
    redacted_url: str | None = None


class CameraStatusOut(ORMModel):
    id: str
    name: str
    status: CameraStatus
    enabled: bool
    streaming: bool = False
    last_heartbeat: datetime | None
    fps: float | None
    connection_error: str | None
    last_frame_at: datetime | None
    retry_count: int


class CameraHealthOut(ORMModel):
    id: str
    name: str
    status: CameraStatus
    enabled: bool
    streaming: bool
    rtsp_configured: bool
    last_heartbeat: datetime | None
    last_frame_at: datetime | None
    fps: float | None
    retry_count: int
    connection_error: str | None
    resolution: str


class EnableBody(ORMModel):
    enabled: bool = Field(...)


class EdgeCameraConfigOut(ORMModel):
    """Returned only to authenticated edge agents. Includes RTSP URL for local capture."""

    id: str
    name: str
    camera_code: str
    direction: Direction
    enabled: bool
    streaming: bool
    frame_interval: float = 0.5
    rtsp_url: str
    anpr_roi: AnprRoi | None = None
    # Never include a separate password field — credentials are embedded in rtsp_url if needed.


class CalibrationSummaryOut(ORMModel):
    status: str
    overall_score: float = 0.0
    component_scores: dict[str, float] = Field(default_factory=dict)
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


class AnprCalibrationStoredOut(ORMModel):
    updated_at: str
    latest: CalibrationSummaryOut
    previous: CalibrationSummaryOut | None = None


class CameraCalibrateResponse(ORMModel):
    """Full calibration report from a test-frame upload (no event created)."""

    camera_id: str
    status: str
    overall_score: float = 0.0
    component_scores: dict[str, float] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    guidance: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    targets: dict[str, Any] = Field(default_factory=dict)
    overlays: dict[str, Any] = Field(default_factory=dict)
    anpr_calibration: AnprCalibrationStoredOut
    previous: CalibrationSummaryOut | None = None
