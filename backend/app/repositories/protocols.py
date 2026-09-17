from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.domain.connectivity import GatewayRecord, NvrRecord, SiteConnectivityRecord
from app.domain.records import (
    AnprEventRecord,
    CameraRecord,
    OrganizationRecord,
    SiteRecord,
    UserProfileRecord,
    VehicleRecord,
)


class GatewayRepository(Protocol):
    async def get(self, gateway_id: str) -> GatewayRecord | None: ...

    async def list_for_tenant(
        self,
        *,
        organization_id: str | None,
        site_ids: list[str] | None,
        site_id: str | None = None,
    ) -> list[GatewayRecord]: ...

    async def add(self, record: GatewayRecord) -> GatewayRecord: ...

    async def save(self, record: GatewayRecord) -> GatewayRecord: ...

    async def delete(self, gateway_id: str) -> None: ...


class NvrRepository(Protocol):
    async def get(self, nvr_id: str) -> NvrRecord | None: ...

    async def list_for_tenant(
        self,
        *,
        organization_id: str | None,
        site_ids: list[str] | None,
        site_id: str | None = None,
    ) -> list[NvrRecord]: ...

    async def add(self, record: NvrRecord) -> NvrRecord: ...

    async def save(self, record: NvrRecord) -> NvrRecord: ...

    async def delete(self, nvr_id: str) -> None: ...


class SiteConnectivityRepository(Protocol):
    async def get(self, site_id: str) -> SiteConnectivityRecord | None: ...

    async def save(self, record: SiteConnectivityRecord) -> SiteConnectivityRecord: ...


class OrganizationRepository(Protocol):
    async def get(self, organization_id: str) -> OrganizationRecord | None: ...

    async def list_all(self) -> list[OrganizationRecord]: ...


class SiteRepository(Protocol):
    async def get(self, site_id: str) -> SiteRecord | None: ...

    async def list_for_org(self, organization_id: str) -> list[SiteRecord]: ...


class UserProfileRepository(Protocol):
    async def get(self, user_id: str) -> UserProfileRecord | None: ...

    async def list_for_org(self, organization_id: str) -> list[UserProfileRecord]: ...


class CameraRepository(Protocol):
    async def get(self, camera_id: str) -> CameraRecord | None: ...

    async def list_for_tenant(
        self,
        *,
        organization_id: str | None,
        site_ids: list[str] | None,
        site_id: str | None = None,
    ) -> list[CameraRecord]: ...


class VehicleRepository(Protocol):
    async def get(self, vehicle_id: str) -> VehicleRecord | None: ...

    async def get_by_plate(self, organization_id: str, plate_normalized: str) -> VehicleRecord | None: ...


class AnprEventRepository(Protocol):
    """Event lists MUST use limit/pagination — never unbounded collection scans."""

    async def get(self, event_id: str) -> AnprEventRecord | None: ...

    async def list_for_tenant(
        self,
        *,
        organization_id: str,
        site_ids: list[str] | None = None,
        site_id: str | None = None,
        limit: int = 50,
        start_after: datetime | None = None,
    ) -> list[AnprEventRecord]: ...
