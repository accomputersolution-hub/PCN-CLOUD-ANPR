from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import VehicleRegistryCategory
from app.schemas.common import ORMModel


class VehicleRegistryCreate(BaseModel):
    plate: str = Field(min_length=4, max_length=32)
    category: VehicleRegistryCategory = VehicleRegistryCategory.RESIDENT
    person_name: str = Field(min_length=1, max_length=200)
    mobile_number: str | None = Field(default=None, max_length=32)
    flat_room_unit: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=500)
    active: bool = True


class VehicleRegistryUpdate(BaseModel):
    category: VehicleRegistryCategory | None = None
    person_name: str | None = Field(default=None, min_length=1, max_length=200)
    mobile_number: str | None = Field(default=None, max_length=32)
    flat_room_unit: str | None = Field(default=None, max_length=64)
    notes: str | None = Field(default=None, max_length=500)
    active: bool | None = None


class VehicleRegistryOut(ORMModel):
    id: str
    organization_id: str
    site_id: str
    plate_normalized: str
    vehicle_id: str | None = None
    category: str
    person_name: str | None = None
    mobile_number: str | None = None
    flat_room_unit: str | None = None
    notes: str | None = None
    active: bool = True
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RegistryMatchOut(BaseModel):
    """ANPR / lookup / Events display result for a plate at a site."""

    known: bool
    status: str  # resident|guest|staff|vendor|unknown
    registry_status: str = "unknown"  # active|inactive|unknown (Events display)
    plate_normalized: str
    person_name: str | None = None
    flat_room_unit: str | None = None
    mobile_number: str | None = None
    registration_id: str | None = None
    active: bool | None = None
    category: str | None = None
