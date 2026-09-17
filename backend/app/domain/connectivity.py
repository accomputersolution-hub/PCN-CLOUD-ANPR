"""Datastore-agnostic connectivity records.

SQLAlchemy maps these today. A Firestore adapter can persist the same fields
as documents keyed by id with organization_id / site_id for tenant queries.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GatewayRecord(BaseModel):
    id: str
    organization_id: str
    site_id: str
    name: str
    device_type: str
    vendor: str = "GENERIC"
    model: str = ""
    firmware_version: str | None = None
    vpn_status: str = "UNKNOWN"
    health_status: str = "UNKNOWN"
    provisioning_status: str = "UNPROVISIONED"
    last_seen: datetime | None = None
    lan_subnet: str | None = None
    capabilities: dict[str, Any] = Field(default_factory=dict)
    device_key_hash: str | None = None
    key_rotated_at: datetime | None = None
    revoked_at: datetime | None = None
    is_active: bool = True
    last_error: str | None = None
    config_version: int = 1
    notes: str = ""
    cpu_usage: float | None = None
    memory_usage: float | None = None


class NvrRecord(BaseModel):
    id: str
    organization_id: str
    site_id: str
    gateway_id: str | None = None
    name: str
    vendor: str = "GENERIC"
    model: str = ""
    host: str = ""
    channel_count: int = 0
    enabled: bool = True
    notes: str = ""


class SiteConnectivityRecord(BaseModel):
    site_id: str
    organization_id: str
    connectivity_mode: str
    anpr_deployment_mode: str
    primary_gateway_id: str | None = None
