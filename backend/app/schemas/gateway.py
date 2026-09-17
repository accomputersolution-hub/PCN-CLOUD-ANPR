from datetime import datetime
from typing import Any

from pydantic import Field

from app.models.enums import (
    GatewayDeviceType,
    GatewayHealthStatus,
    GatewayProvisioningStatus,
    VpnStatus,
)
from app.schemas.common import ORMModel


class GatewayCreate(ORMModel):
    site_id: str
    name: str = Field(min_length=1, max_length=200)
    device_type: GatewayDeviceType
    vendor: str = Field(default="GENERIC", max_length=80)
    model: str = Field(default="", max_length=80)
    firmware_version: str | None = Field(default=None, max_length=64)
    lan_subnet: str | None = Field(default=None, max_length=64)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class GatewayUpdate(ORMModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    vendor: str | None = Field(default=None, max_length=80)
    model: str | None = Field(default=None, max_length=80)
    firmware_version: str | None = None
    lan_subnet: str | None = None
    capabilities: dict[str, Any] | None = None
    notes: str | None = None
    is_active: bool | None = None


class GatewayHeartbeatRequest(ORMModel):
    vpn_status: VpnStatus | None = None
    health_status: GatewayHealthStatus | None = None
    firmware_version: str | None = None
    lan_subnet: str | None = None
    cpu_usage: float | None = None
    memory_usage: float | None = None
    last_error: str | None = None
    capabilities: dict[str, Any] | None = None


class GatewayOut(ORMModel):
    id: str
    organization_id: str
    site_id: str
    name: str
    device_type: GatewayDeviceType
    vendor: str
    model: str
    firmware_version: str | None
    vpn_status: VpnStatus
    health_status: GatewayHealthStatus
    provisioning_status: GatewayProvisioningStatus
    last_seen: datetime | None
    lan_subnet: str | None
    capabilities: dict[str, Any]
    is_active: bool
    last_error: str | None
    config_version: int
    notes: str
    cpu_usage: float | None
    memory_usage: float | None
    revoked: bool = False
    camera_count: int = 0
    nvr_count: int = 0
    site_name: str | None = None


class GatewayCreated(GatewayOut):
    device_key: str
    message: str = "Store the device key securely; it cannot be recovered."
