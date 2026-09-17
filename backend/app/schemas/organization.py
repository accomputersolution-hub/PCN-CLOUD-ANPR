from pydantic import Field

from app.schemas.common import ORMModel


class OrganizationCreate(ORMModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str = Field(min_length=2, max_length=80, pattern=r"^[a-z0-9-]+$")
    retention_days: int = Field(default=90, ge=1, le=3650)


class OrganizationUpdate(ORMModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    is_active: bool | None = None


class OrganizationOut(ORMModel):
    id: str
    name: str
    slug: str
    retention_days: int
    is_active: bool
