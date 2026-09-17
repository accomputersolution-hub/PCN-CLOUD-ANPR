from datetime import datetime

from pydantic import Field

from app.models.enums import Direction, SourceType, SyncStatus
from app.schemas.common import ORMModel


class EventOut(ORMModel):
    id: str
    organization_id: str
    site_id: str
    gate_id: str
    camera_id: str
    vehicle_id: str | None
    visit_id: str | None
    direction: Direction
    plate_text: str
    raw_ocr_text: str
    plate_normalized: str
    ocr_confidence: float
    plate_detection_confidence: float
    vehicle_detection_confidence: float
    timestamp: datetime
    local_timestamp: datetime
    snapshot_path: str | None
    plate_crop_path: str | None
    vehicle_crop_path: str | None
    processing_duration_ms: int
    source_type: SourceType
    sync_status: SyncStatus
    classification: str | None
    notes: str | None
    camera_name: str | None = None
    gate_name: str | None = None
    site_name: str | None = None
    duplicate_suppressed: bool = False


class EventCorrectRequest(ORMModel):
    plate_text: str = Field(min_length=3, max_length=32)


class EventClassifyRequest(ORMModel):
    classification: str = Field(min_length=1, max_length=64)
    notes: str | None = None


class MockEventRequest(ORMModel):
    camera_id: str
    plate_text: str
    direction: Direction | None = None
    ocr_confidence: float = Field(default=0.94, ge=0, le=1)
    plate_detection_confidence: float = Field(default=0.91, ge=0, le=1)
    vehicle_detection_confidence: float = Field(default=0.88, ge=0, le=1)
    source_type: SourceType = SourceType.MOCK
    event_id: str | None = None
