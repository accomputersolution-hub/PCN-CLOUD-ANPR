from datetime import datetime

from app.schemas.common import ORMModel
from app.schemas.event import EventOut


class DashboardSummary(ORMModel):
    entries_today: int
    exits_today: int
    currently_inside: int
    detections_today: int
    cameras_active: int
    cameras_offline: int
    cameras_total: int
    timezone: str
    recent_events: list[EventOut]


class SystemHealth(ORMModel):
    api: str
    database: str
    storage: str
    time: datetime
    camera_offline_count: int
    edge_connected_count: int
