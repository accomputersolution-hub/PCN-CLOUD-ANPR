from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class MessageResponse(BaseModel):
    message: str


class PageMeta(BaseModel):
    total: int
    page: int
    page_size: int


class Paginated(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    service: str
    time: datetime
