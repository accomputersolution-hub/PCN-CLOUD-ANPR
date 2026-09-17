from datetime import datetime

from pydantic import BaseModel

from app.models.enums import VisitStatus
from app.schemas.common import ORMModel
from app.schemas.event import EventOut


class VehicleOut(ORMModel):
    id: str
    organization_id: str
    plate_normalized: str
    first_seen: datetime
    last_seen: datetime
    total_visits: int
    currently_inside: bool
    visitor_note: str | None
    classification: str | None


class VehicleVisitorUpdate(BaseModel):
    visitor_note: str | None = None
    classification: str | None = None


class VisitOut(ORMModel):
    id: str
    organization_id: str
    site_id: str
    vehicle_id: str
    plate_normalized: str
    entry_event_id: str | None
    exit_event_id: str | None
    entry_at: datetime | None
    exit_at: datetime | None
    gate_id: str | None
    duration_seconds: int | None
    status: VisitStatus
    duration_label: str | None = None


class VisitResolveRequest(BaseModel):
    status: VisitStatus
    notes: str | None = None


class VehicleDetail(BaseModel):
    vehicle: VehicleOut
    visits: list[VisitOut]
    events: list[EventOut]
