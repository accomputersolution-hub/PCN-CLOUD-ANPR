from app.models.enums import GateMode
from app.schemas.common import ORMModel


class GateCreate(ORMModel):
    site_id: str
    name: str
    mode: GateMode = GateMode.MIXED


class GateUpdate(ORMModel):
    name: str | None = None
    mode: GateMode | None = None


class GateOut(ORMModel):
    id: str
    organization_id: str
    site_id: str
    name: str
    mode: GateMode
