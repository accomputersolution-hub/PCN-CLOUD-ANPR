"""Firestore repository stubs.

Selected only when DATASTORE_PROVIDER=firestore. Production code raises until
the real Admin SDK adapter is implemented — no fake empty successes.
"""

from __future__ import annotations

from datetime import datetime

from app.core.exceptions import ValidationAppError
from app.core.providers import require_firebase_admin_for
from app.domain.connectivity import GatewayRecord, NvrRecord, SiteConnectivityRecord
from app.domain.records import (
    AnprEventRecord,
    CameraRecord,
    OrganizationRecord,
    SiteRecord,
    UserProfileRecord,
    VehicleRecord,
)


def _not_enabled(entity: str) -> None:
    require_firebase_admin_for(f"Firestore {entity}")
    raise ValidationAppError(
        f"Firestore {entity} repository is not enabled yet. "
        "Keep DATASTORE_PROVIDER=sqlalchemy until the adapter is implemented.",
    )


class FirestoreGatewayRepository:
    async def get(self, gateway_id: str) -> GatewayRecord | None:
        _not_enabled("gateways")
        return None

    async def list_for_tenant(
        self,
        *,
        organization_id: str | None,
        site_ids: list[str] | None,
        site_id: str | None = None,
    ) -> list[GatewayRecord]:
        _not_enabled("gateways")
        return []

    async def add(self, record: GatewayRecord) -> GatewayRecord:
        _not_enabled("gateways")
        return record

    async def save(self, record: GatewayRecord) -> GatewayRecord:
        _not_enabled("gateways")
        return record

    async def delete(self, gateway_id: str) -> None:
        _not_enabled("gateways")


class FirestoreNvrRepository:
    async def get(self, nvr_id: str) -> NvrRecord | None:
        _not_enabled("nvrs")
        return None

    async def list_for_tenant(
        self,
        *,
        organization_id: str | None,
        site_ids: list[str] | None,
        site_id: str | None = None,
    ) -> list[NvrRecord]:
        _not_enabled("nvrs")
        return []

    async def add(self, record: NvrRecord) -> NvrRecord:
        _not_enabled("nvrs")
        return record

    async def save(self, record: NvrRecord) -> NvrRecord:
        _not_enabled("nvrs")
        return record

    async def delete(self, nvr_id: str) -> None:
        _not_enabled("nvrs")


class FirestoreSiteConnectivityRepository:
    async def get(self, site_id: str) -> SiteConnectivityRecord | None:
        _not_enabled("site connectivity")
        return None

    async def save(self, record: SiteConnectivityRecord) -> SiteConnectivityRecord:
        _not_enabled("site connectivity")
        return record


class FirestoreOrganizationRepository:
    async def get(self, organization_id: str) -> OrganizationRecord | None:
        _not_enabled("organizations")
        return None

    async def list_all(self) -> list[OrganizationRecord]:
        _not_enabled("organizations")
        return []


class FirestoreSiteRepository:
    async def get(self, site_id: str) -> SiteRecord | None:
        _not_enabled("sites")
        return None

    async def list_for_org(self, organization_id: str) -> list[SiteRecord]:
        _not_enabled("sites")
        return []


class FirestoreUserProfileRepository:
    async def get(self, user_id: str) -> UserProfileRecord | None:
        _not_enabled("user profiles")
        return None

    async def list_for_org(self, organization_id: str) -> list[UserProfileRecord]:
        _not_enabled("user profiles")
        return []


class FirestoreCameraRepository:
    async def get(self, camera_id: str) -> CameraRecord | None:
        _not_enabled("cameras")
        return None

    async def list_for_tenant(
        self,
        *,
        organization_id: str | None,
        site_ids: list[str] | None,
        site_id: str | None = None,
    ) -> list[CameraRecord]:
        _not_enabled("cameras")
        return []


class FirestoreVehicleRepository:
    async def get(self, vehicle_id: str) -> VehicleRecord | None:
        _not_enabled("vehicles")
        return None

    async def get_by_plate(self, organization_id: str, plate_normalized: str) -> VehicleRecord | None:
        _not_enabled("vehicles")
        return None


class FirestoreAnprEventRepository:
    async def get(self, event_id: str) -> AnprEventRecord | None:
        _not_enabled("anpr events")
        return None

    async def list_for_tenant(
        self,
        *,
        organization_id: str,
        site_ids: list[str] | None = None,
        site_id: str | None = None,
        limit: int = 50,
        start_after: datetime | None = None,
    ) -> list[AnprEventRecord]:
        _not_enabled("anpr events")
        return []
