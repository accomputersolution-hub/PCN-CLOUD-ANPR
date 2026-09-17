from typing import Any

from pydantic import Field

from app.schemas.common import ORMModel


class SiteCreate(ORMModel):
    organization_id: str | None = None
    name: str = Field(min_length=1, max_length=200)
    address: str = ""
    timezone: str = "Asia/Kolkata"
    settings: dict[str, Any] | None = None


class SiteUpdate(ORMModel):
    name: str | None = None
    address: str | None = None
    timezone: str | None = None
    settings: dict[str, Any] | None = None
    is_active: bool | None = None


class SiteOut(ORMModel):
    id: str
    organization_id: str
    name: str
    address: str
    timezone: str
    settings: dict[str, Any]
    is_active: bool
