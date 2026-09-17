from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import Direction, SourceType


class EdgeRegisterRequest(BaseModel):
    name: str
    site_id: str
    agent_key: str = Field(min_length=12)


class EdgeRegisterResponse(BaseModel):
    agent_id: str
    site_id: str
    organization_id: str
    message: str


class EdgeHeartbeatRequest(BaseModel):
    cpu_usage: float | None = None
    memory_usage: float | None = None
    queue_size: int = 0
    camera_statuses: list[dict] = Field(default_factory=list)


class EdgeEventIn(BaseModel):
    id: str
    camera_id: str
    direction: Direction
    plate_text: str
    raw_ocr_text: str | None = None
    ocr_confidence: float
    plate_detection_confidence: float = 0
    vehicle_detection_confidence: float = 0
    timestamp: datetime
    local_timestamp: datetime | None = None
    processing_duration_ms: int = 0
    source_type: SourceType = SourceType.EDGE
    snapshot_b64: str | None = None
    plate_crop_b64: str | None = None
    vehicle_crop_b64: str | None = None


class EdgeSyncRequest(BaseModel):
    events: list[EdgeEventIn]


class EdgeSyncResponse(BaseModel):
    accepted: int
    duplicates: int
    rejected: int
    event_ids: list[str]
