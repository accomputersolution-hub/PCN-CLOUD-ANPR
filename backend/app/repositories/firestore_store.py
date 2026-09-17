"""Firestore repositories via Firebase Admin SDK.

Selected when DATASTORE_PROVIDER=firestore. Requires FIREBASE_* Admin config.
No fake empty successes when misconfigured. Images never stored here — only
metadata and storage object keys.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, TypeVar

from app.core.exceptions import ValidationAppError
from app.core.providers import require_firebase_admin_for
from app.domain.connectivity import GatewayRecord, NvrRecord, SiteConnectivityRecord
from app.domain.records import (
    AnprEventRecord,
    CameraRecord,
    EdgeAgentRecord,
    GateRecord,
    OrganizationRecord,
    RefreshTokenRecord,
    SiteRecord,
    UserProfileRecord,
    VehicleRecord,
    VisitRecord,
)
from app.firebase.cost_controls import clamp_page_size
from app.firebase.firestore_client import get_firestore_client, tenant_filter_ok

T = TypeVar("T")

_BANNED_BINARY_KEYS = (
    "image_bytes",
    "frame_bytes",
    "snapshot_bytes",
    "jpeg",
    "plate_crop_bytes",
    "vehicle_crop_bytes",
    "thumbnail_bytes",
)


def _ensure_ready(entity: str) -> Any:
    require_firebase_admin_for(f"Firestore {entity}")
    return get_firestore_client()


async def _to_thread(fn, *args, **kwargs):
    return await asyncio.to_thread(fn, *args, **kwargs)


def _dump(model: Any) -> dict[str, Any]:
    data = model.model_dump(mode="json")
    data["id"] = model.id
    for banned in _BANNED_BINARY_KEYS:
        data.pop(banned, None)
    return data


def _strip_binary(data: dict[str, Any]) -> dict[str, Any]:
    for banned in _BANNED_BINARY_KEYS:
        data.pop(banned, None)
    # Drop any leftover raw byte payloads under common aliases.
    for key in list(data.keys()):
        if key.endswith("_bytes") or key.endswith("_jpeg"):
            data.pop(key, None)
    return data


def _get_doc(db: Any, collection: str, doc_id: str) -> dict[str, Any] | None:
    snap = db.collection(collection).document(doc_id).get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    data["id"] = snap.id
    return _strip_binary(data)


def _set_doc(db: Any, collection: str, doc_id: str, payload: dict[str, Any]) -> None:
    body = {k: v for k, v in payload.items() if k != "id"}
    for banned in _BANNED_BINARY_KEYS:
        body.pop(banned, None)
    db.collection(collection).document(doc_id).set(body, merge=True)


def _delete_doc(db: Any, collection: str, doc_id: str) -> None:
    db.collection(collection).document(doc_id).delete()


def _query_org(
    db: Any,
    collection: str,
    *,
    organization_id: str | None,
    site_ids: list[str] | None = None,
    site_id: str | None = None,
    limit: int | None = None,
    order_by: str | None = None,
    descending: bool = False,
) -> list[dict[str, Any]]:
    col = db.collection(collection)
    query = col
    if organization_id:
        query = query.where("organization_id", "==", organization_id)
    if site_id:
        query = query.where("site_id", "==", site_id)
    if order_by:
        direction = "DESCENDING" if descending else "ASCENDING"
        query = query.order_by(order_by, direction=direction)
    page = clamp_page_size(limit)
    query = query.limit(page)
    out: list[dict[str, Any]] = []
    for snap in query.stream():
        data = snap.to_dict() or {}
        data["id"] = snap.id
        data = _strip_binary(data)
        if tenant_filter_ok(data, organization_id=organization_id, site_ids=site_ids, site_id=site_id):
            out.append(data)
    return out


class FirestoreOrganizationRepository:
    async def get(self, organization_id: str) -> OrganizationRecord | None:
        db = _ensure_ready("organizations")
        data = await _to_thread(_get_doc, db, "organizations", organization_id)
        return OrganizationRecord.model_validate(data) if data else None

    async def list_all(self) -> list[OrganizationRecord]:
        db = _ensure_ready("organizations")

        def _list() -> list[dict[str, Any]]:
            out = []
            for snap in db.collection("organizations").limit(200).stream():
                data = snap.to_dict() or {}
                data["id"] = snap.id
                out.append(data)
            return out

        rows = await _to_thread(_list)
        return [OrganizationRecord.model_validate(r) for r in rows]

    async def get_by_slug(self, slug: str) -> OrganizationRecord | None:
        db = _ensure_ready("organizations")

        def _find() -> dict[str, Any] | None:
            q = db.collection("organizations").where("slug", "==", slug).limit(1)
            for snap in q.stream():
                data = snap.to_dict() or {}
                data["id"] = snap.id
                return data
            return None

        data = await _to_thread(_find)
        return OrganizationRecord.model_validate(data) if data else None

    async def add(self, record: OrganizationRecord) -> OrganizationRecord:
        db = _ensure_ready("organizations")
        await _to_thread(_set_doc, db, "organizations", record.id, _dump(record))
        return record

    async def save(self, record: OrganizationRecord) -> OrganizationRecord:
        return await self.add(record)

    async def delete(self, organization_id: str) -> None:
        db = _ensure_ready("organizations")
        await _to_thread(_delete_doc, db, "organizations", organization_id)


class FirestoreSiteRepository:
    async def get(self, site_id: str) -> SiteRecord | None:
        db = _ensure_ready("sites")
        data = await _to_thread(_get_doc, db, "sites", site_id)
        return SiteRecord.model_validate(data) if data else None

    async def list_for_org(self, organization_id: str) -> list[SiteRecord]:
        db = _ensure_ready("sites")
        rows = await _to_thread(
            _query_org,
            db,
            "sites",
            organization_id=organization_id,
            limit=100,
        )
        return [SiteRecord.model_validate(r) for r in rows]

    async def list_all_limited(self, limit: int = 100) -> list[SiteRecord]:
        db = _ensure_ready("sites")
        page = clamp_page_size(limit)

        def _list() -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for snap in db.collection("sites").limit(page).stream():
                data = snap.to_dict() or {}
                data["id"] = snap.id
                out.append(data)
            return out

        rows = await _to_thread(_list)
        return [SiteRecord.model_validate(r) for r in rows]

    async def add(self, record: SiteRecord) -> SiteRecord:
        db = _ensure_ready("sites")
        await _to_thread(_set_doc, db, "sites", record.id, _dump(record))
        return record

    async def save(self, record: SiteRecord) -> SiteRecord:
        return await self.add(record)

    async def delete(self, site_id: str) -> None:
        db = _ensure_ready("sites")
        await _to_thread(_delete_doc, db, "sites", site_id)


class FirestoreUserProfileRepository:
    async def get(self, user_id: str) -> UserProfileRecord | None:
        db = _ensure_ready("user profiles")
        data = await _to_thread(_get_doc, db, "users", user_id)
        return UserProfileRecord.model_validate(data) if data else None

    async def get_by_email(self, email: str) -> UserProfileRecord | None:
        db = _ensure_ready("user profiles")
        normalized = email.strip().lower()

        def _find() -> dict[str, Any] | None:
            q = db.collection("users").where("email", "==", normalized).limit(1)
            for snap in q.stream():
                data = snap.to_dict() or {}
                data["id"] = snap.id
                return data
            return None

        data = await _to_thread(_find)
        return UserProfileRecord.model_validate(data) if data else None

    async def list_for_org(self, organization_id: str) -> list[UserProfileRecord]:
        db = _ensure_ready("user profiles")
        rows = await _to_thread(
            _query_org,
            db,
            "users",
            organization_id=organization_id,
            limit=100,
        )
        return [UserProfileRecord.model_validate(r) for r in rows]

    async def add(self, record: UserProfileRecord) -> UserProfileRecord:
        db = _ensure_ready("user profiles")
        await _to_thread(_set_doc, db, "users", record.id, _dump(record))
        return record

    async def save(self, record: UserProfileRecord) -> UserProfileRecord:
        return await self.add(record)


class FirestoreGateRepository:
    async def get(self, gate_id: str) -> GateRecord | None:
        db = _ensure_ready("gates")
        data = await _to_thread(_get_doc, db, "gates", gate_id)
        return GateRecord.model_validate(data) if data else None

    async def list_for_tenant(
        self,
        *,
        organization_id: str | None,
        site_ids: list[str] | None = None,
        site_id: str | None = None,
    ) -> list[GateRecord]:
        db = _ensure_ready("gates")
        rows = await _to_thread(
            _query_org,
            db,
            "gates",
            organization_id=organization_id,
            site_ids=site_ids,
            site_id=site_id,
            limit=100,
        )
        return [GateRecord.model_validate(r) for r in rows]

    async def add(self, record: GateRecord) -> GateRecord:
        db = _ensure_ready("gates")
        await _to_thread(_set_doc, db, "gates", record.id, _dump(record))
        return record

    async def save(self, record: GateRecord) -> GateRecord:
        return await self.add(record)

    async def delete(self, gate_id: str) -> None:
        db = _ensure_ready("gates")
        await _to_thread(_delete_doc, db, "gates", gate_id)


class FirestoreGatewayRepository:
    async def get(self, gateway_id: str) -> GatewayRecord | None:
        db = _ensure_ready("gateways")
        data = await _to_thread(_get_doc, db, "gateways", gateway_id)
        return GatewayRecord.model_validate(data) if data else None

    async def list_for_tenant(
        self,
        *,
        organization_id: str | None,
        site_ids: list[str] | None,
        site_id: str | None = None,
    ) -> list[GatewayRecord]:
        db = _ensure_ready("gateways")
        rows = await _to_thread(
            _query_org,
            db,
            "gateways",
            organization_id=organization_id,
            site_ids=site_ids,
            site_id=site_id,
            limit=100,
        )
        return [GatewayRecord.model_validate(r) for r in rows]

    async def add(self, record: GatewayRecord) -> GatewayRecord:
        db = _ensure_ready("gateways")
        await _to_thread(_set_doc, db, "gateways", record.id, _dump(record))
        return record

    async def save(self, record: GatewayRecord) -> GatewayRecord:
        return await self.add(record)

    async def delete(self, gateway_id: str) -> None:
        db = _ensure_ready("gateways")
        await _to_thread(_delete_doc, db, "gateways", gateway_id)


class FirestoreNvrRepository:
    async def get(self, nvr_id: str) -> NvrRecord | None:
        db = _ensure_ready("nvrs")
        data = await _to_thread(_get_doc, db, "nvrs", nvr_id)
        return NvrRecord.model_validate(data) if data else None

    async def list_for_tenant(
        self,
        *,
        organization_id: str | None,
        site_ids: list[str] | None,
        site_id: str | None = None,
    ) -> list[NvrRecord]:
        db = _ensure_ready("nvrs")
        rows = await _to_thread(
            _query_org,
            db,
            "nvrs",
            organization_id=organization_id,
            site_ids=site_ids,
            site_id=site_id,
            limit=100,
        )
        return [NvrRecord.model_validate(r) for r in rows]

    async def add(self, record: NvrRecord) -> NvrRecord:
        db = _ensure_ready("nvrs")
        await _to_thread(_set_doc, db, "nvrs", record.id, _dump(record))
        return record

    async def save(self, record: NvrRecord) -> NvrRecord:
        return await self.add(record)

    async def delete(self, nvr_id: str) -> None:
        db = _ensure_ready("nvrs")
        await _to_thread(_delete_doc, db, "nvrs", nvr_id)


class FirestoreSiteConnectivityRepository:
    async def get(self, site_id: str) -> SiteConnectivityRecord | None:
        db = _ensure_ready("site connectivity")
        data = await _to_thread(_get_doc, db, "sites", site_id)
        if not data:
            return None
        return SiteConnectivityRecord(
            site_id=site_id,
            organization_id=data["organization_id"],
            connectivity_mode=data.get("connectivity_mode", "EXISTING_VPN_ROUTER"),
            anpr_deployment_mode=data.get("anpr_deployment_mode", "LOCAL_EDGE_AGENT"),
            primary_gateway_id=data.get("primary_gateway_id"),
        )

    async def save(self, record: SiteConnectivityRecord) -> SiteConnectivityRecord:
        db = _ensure_ready("site connectivity")

        def _merge() -> None:
            ref = db.collection("sites").document(record.site_id)
            snap = ref.get()
            payload = {
                "organization_id": record.organization_id,
                "connectivity_mode": record.connectivity_mode,
                "anpr_deployment_mode": record.anpr_deployment_mode,
                "primary_gateway_id": record.primary_gateway_id,
            }
            if snap.exists:
                ref.set(payload, merge=True)
            else:
                raise ValidationAppError(f"Site {record.site_id} not found in Firestore")

        await _to_thread(_merge)
        return record


class FirestoreCameraRepository:
    async def get(self, camera_id: str) -> CameraRecord | None:
        db = _ensure_ready("cameras")
        data = await _to_thread(_get_doc, db, "cameras", camera_id)
        return CameraRecord.model_validate(data) if data else None

    async def list_for_tenant(
        self,
        *,
        organization_id: str | None,
        site_ids: list[str] | None,
        site_id: str | None = None,
    ) -> list[CameraRecord]:
        db = _ensure_ready("cameras")
        rows = await _to_thread(
            _query_org,
            db,
            "cameras",
            organization_id=organization_id,
            site_ids=site_ids,
            site_id=site_id,
            limit=100,
        )
        return [CameraRecord.model_validate(r) for r in rows]

    async def add(self, record: CameraRecord) -> CameraRecord:
        db = _ensure_ready("cameras")
        # Encrypted credential fields are allowed; binary image payloads are stripped by _dump.
        await _to_thread(_set_doc, db, "cameras", record.id, _dump(record))
        return record

    async def save(self, record: CameraRecord) -> CameraRecord:
        return await self.add(record)

    async def delete(self, camera_id: str) -> None:
        db = _ensure_ready("cameras")
        await _to_thread(_delete_doc, db, "cameras", camera_id)


class FirestoreVehicleRepository:
    async def get(self, vehicle_id: str) -> VehicleRecord | None:
        db = _ensure_ready("vehicles")
        data = await _to_thread(_get_doc, db, "vehicles", vehicle_id)
        return VehicleRecord.model_validate(data) if data else None

    async def get_by_plate(self, organization_id: str, plate_normalized: str) -> VehicleRecord | None:
        db = _ensure_ready("vehicles")

        def _find() -> dict[str, Any] | None:
            q = (
                db.collection("vehicles")
                .where("organization_id", "==", organization_id)
                .where("plate_normalized", "==", plate_normalized)
                .limit(1)
            )
            for snap in q.stream():
                data = snap.to_dict() or {}
                data["id"] = snap.id
                return data
            return None

        data = await _to_thread(_find)
        return VehicleRecord.model_validate(data) if data else None

    async def list_for_org(self, organization_id: str, limit: int = 100) -> list[VehicleRecord]:
        db = _ensure_ready("vehicles")
        rows = await _to_thread(
            _query_org,
            db,
            "vehicles",
            organization_id=organization_id,
            limit=limit,
        )
        return [VehicleRecord.model_validate(r) for r in rows]

    async def add(self, record: VehicleRecord) -> VehicleRecord:
        db = _ensure_ready("vehicles")
        await _to_thread(_set_doc, db, "vehicles", record.id, _dump(record))
        return record

    async def save(self, record: VehicleRecord) -> VehicleRecord:
        return await self.add(record)


class FirestoreAnprEventRepository:
    async def get(self, event_id: str) -> AnprEventRecord | None:
        db = _ensure_ready("anpr events")
        data = await _to_thread(_get_doc, db, "anprEvents", event_id)
        return AnprEventRecord.model_validate(data) if data else None

    async def list_for_tenant(
        self,
        *,
        organization_id: str,
        site_ids: list[str] | None = None,
        site_id: str | None = None,
        limit: int = 50,
        start_after: datetime | None = None,
    ) -> list[AnprEventRecord]:
        db = _ensure_ready("anpr events")
        page = clamp_page_size(limit)

        def _list() -> list[dict[str, Any]]:
            q = (
                db.collection("anprEvents")
                .where("organization_id", "==", organization_id)
                .order_by("timestamp", direction="DESCENDING")
                .limit(page)
            )
            if site_id:
                q = (
                    db.collection("anprEvents")
                    .where("organization_id", "==", organization_id)
                    .where("site_id", "==", site_id)
                    .order_by("timestamp", direction="DESCENDING")
                    .limit(page)
                )
            if start_after is not None:
                q = q.start_after({"timestamp": start_after})
            out: list[dict[str, Any]] = []
            for snap in q.stream():
                data = snap.to_dict() or {}
                data["id"] = snap.id
                data = _strip_binary(data)
                if tenant_filter_ok(data, organization_id=organization_id, site_ids=site_ids, site_id=site_id):
                    out.append(data)
            return out

        rows = await _to_thread(_list)
        return [AnprEventRecord.model_validate(r) for r in rows]

    async def add(self, record: AnprEventRecord) -> AnprEventRecord:
        db = _ensure_ready("anpr events")
        payload = _dump(record)
        await _to_thread(_set_doc, db, "anprEvents", record.id, payload)
        return record

    async def save(self, record: AnprEventRecord) -> AnprEventRecord:
        return await self.add(record)

    async def delete(self, event_id: str) -> None:
        db = _ensure_ready("anpr events")
        await _to_thread(_delete_doc, db, "anprEvents", event_id)


class FirestoreEdgeAgentRepository:
    async def get(self, agent_id: str) -> EdgeAgentRecord | None:
        db = _ensure_ready("edge agents")
        data = await _to_thread(_get_doc, db, "edgeAgents", agent_id)
        return EdgeAgentRecord.model_validate(data) if data else None

    async def list_for_tenant(
        self,
        *,
        organization_id: str | None,
        site_ids: list[str] | None = None,
        site_id: str | None = None,
    ) -> list[EdgeAgentRecord]:
        db = _ensure_ready("edge agents")
        rows = await _to_thread(
            _query_org,
            db,
            "edgeAgents",
            organization_id=organization_id,
            site_ids=site_ids,
            site_id=site_id,
            limit=100,
        )
        return [EdgeAgentRecord.model_validate(r) for r in rows]

    async def add(self, record: EdgeAgentRecord) -> EdgeAgentRecord:
        db = _ensure_ready("edge agents")
        await _to_thread(_set_doc, db, "edgeAgents", record.id, _dump(record))
        return record

    async def save(self, record: EdgeAgentRecord) -> EdgeAgentRecord:
        return await self.add(record)

    async def delete(self, agent_id: str) -> None:
        db = _ensure_ready("edge agents")
        await _to_thread(_delete_doc, db, "edgeAgents", agent_id)


class FirestoreVisitRepository:
    async def get(self, visit_id: str) -> VisitRecord | None:
        db = _ensure_ready("vehicle visits")
        data = await _to_thread(_get_doc, db, "vehicleVisits", visit_id)
        return VisitRecord.model_validate(data) if data else None

    async def list_for_tenant(
        self,
        *,
        organization_id: str | None,
        site_ids: list[str] | None = None,
        site_id: str | None = None,
        limit: int = 100,
    ) -> list[VisitRecord]:
        db = _ensure_ready("vehicle visits")
        rows = await _to_thread(
            _query_org,
            db,
            "vehicleVisits",
            organization_id=organization_id,
            site_ids=site_ids,
            site_id=site_id,
            limit=limit,
            order_by="entry_at",
            descending=True,
        )
        return [VisitRecord.model_validate(r) for r in rows]

    async def add(self, record: VisitRecord) -> VisitRecord:
        db = _ensure_ready("vehicle visits")
        await _to_thread(_set_doc, db, "vehicleVisits", record.id, _dump(record))
        return record

    async def save(self, record: VisitRecord) -> VisitRecord:
        return await self.add(record)

    async def delete(self, visit_id: str) -> None:
        db = _ensure_ready("vehicle visits")
        await _to_thread(_delete_doc, db, "vehicleVisits", visit_id)


class FirestoreRefreshTokenRepository:
    async def get_by_hash(self, token_hash: str) -> RefreshTokenRecord | None:
        db = _ensure_ready("refresh tokens")
        data = await _to_thread(_get_doc, db, "refreshTokens", token_hash)
        return RefreshTokenRecord.model_validate(data) if data else None

    async def add(self, record: RefreshTokenRecord) -> RefreshTokenRecord:
        db = _ensure_ready("refresh tokens")
        doc_id = record.id or record.token_hash
        payload = _dump(record)
        payload["id"] = doc_id
        await _to_thread(_set_doc, db, "refreshTokens", doc_id, payload)
        return record

    async def revoke(self, token_hash: str) -> None:
        db = _ensure_ready("refresh tokens")
        existing = await self.get_by_hash(token_hash)
        if existing is None:
            return
        existing.revoked = True
        await self.add(existing)
