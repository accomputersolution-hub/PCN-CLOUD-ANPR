from datetime import datetime

from pydantic import Field

from app.models.enums import CameraSourceType, CameraStatus, Direction, StreamType
from app.schemas.common import ORMModel


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
    # Never include a separate password field — credentials are embedded in rtsp_url if needed.
