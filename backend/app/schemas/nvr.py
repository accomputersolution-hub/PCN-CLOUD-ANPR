from app.schemas.common import ORMModel


class NvrCreate(ORMModel):
    site_id: str
    name: str
    vendor: str = "GENERIC"
    model: str = ""
    host: str = ""
    channel_count: int = 0
    gateway_id: str | None = None
    enabled: bool = True
    notes: str = ""


class NvrUpdate(ORMModel):
    name: str | None = None
    vendor: str | None = None
    model: str | None = None
    host: str | None = None
    channel_count: int | None = None
    gateway_id: str | None = None
    enabled: bool | None = None
    notes: str | None = None


class NvrOut(ORMModel):
    id: str
    organization_id: str
    site_id: str
    gateway_id: str | None
    name: str
    vendor: str
    model: str
    host: str
    channel_count: int
    enabled: bool
    notes: str
    camera_count: int = 0
    site_name: str | None = None
