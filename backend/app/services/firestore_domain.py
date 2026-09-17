"""High-level Firestore domain operations for API routes when DATASTORE_PROVIDER=firestore.

Image bytes are never written to Firestore — only storage object keys.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.core.exceptions import ConflictError, NotFoundError, ValidationAppError
from app.core.security import hash_password
from app.domain.records import (
    AnprEventRecord,
    CameraRecord,
    EdgeAgentRecord,
    GateRecord,
    OrganizationRecord,
    SiteRecord,
    UserProfileRecord,
    VehicleRecord,
    VisitRecord,
)
from app.firebase.cost_controls import event_storage_keys
from app.models.enums import Direction, SourceType, SyncStatus, VisitStatus
from app.models.site import DEFAULT_SITE_SETTINGS
from app.repositories import (
    anpr_event_repo,
    camera_repo,
    edge_agent_repo,
    gate_repo,
    gateway_repo,
    nvr_repo,
    organization_repo,
    site_connectivity_repo,
    site_repo,
    user_profile_repo,
    vehicle_repo,
    visit_repo,
)
from app.domain.connectivity import SiteConnectivityRecord
from app.services.camera import encrypt_optional
from app.services.plate import normalize_plate
from app.services.realtime import hub
from app.services.storage import get_storage
from app.services.tenant import TenantContext


def _site_settings(site: SiteRecord) -> dict[str, Any]:
    return {**DEFAULT_SITE_SETTINGS, **(site.settings or {})}


def _enum_str(value: Any) -> str:
    if value is None:
        return ""
    return value.value if hasattr(value, "value") else str(value)


# --- Organizations -----------------------------------------------------------------


async def list_organizations(ctx: TenantContext) -> list[OrganizationRecord]:
    if ctx.is_super:
        return await organization_repo().list_all()
    if not ctx.organization_id:
        return []
    org = await organization_repo().get(ctx.organization_id)
    return [org] if org else []


async def create_organization(*, name: str, slug: str, retention_days: int) -> OrganizationRecord:
    existing = await organization_repo().get_by_slug(slug)
    if existing:
        raise ConflictError("Organization slug already exists")
    record = OrganizationRecord(
        id=str(uuid4()),
        name=name,
        slug=slug,
        retention_days=retention_days,
        is_active=True,
    )
    return await organization_repo().add(record)


async def update_organization(org_id: str, data: dict[str, Any]) -> OrganizationRecord:
    org = await organization_repo().get(org_id)
    if not org:
        raise NotFoundError("Organization not found")
    for key, value in data.items():
        setattr(org, key, value)
    return await organization_repo().save(org)


# --- Sites ------------------------------------------------------------------------


async def list_sites(
    ctx: TenantContext,
    *,
    organization_id: str | None = None,
) -> list[SiteRecord]:
    if organization_id:
        if not ctx.is_super:
            ctx.ensure_org(organization_id)
        rows = await site_repo().list_for_org(organization_id)
    elif ctx.is_super:
        rows = await site_repo().list_all_limited(100)
    elif ctx.organization_id:
        rows = await site_repo().list_for_org(ctx.organization_id)
    else:
        rows = []
    if ctx.site_ids:
        rows = [s for s in rows if s.id in ctx.site_ids]
    return rows


async def create_site(
    ctx: TenantContext,
    *,
    organization_id: str,
    name: str,
    address: str = "",
    timezone: str = "Asia/Kolkata",
    settings: dict[str, Any] | None = None,
) -> SiteRecord:
    if not ctx.is_super:
        ctx.ensure_org(organization_id)
    record = SiteRecord(
        id=str(uuid4()),
        organization_id=organization_id,
        name=name,
        address=address or "",
        timezone=timezone or "Asia/Kolkata",
        settings=settings or {**DEFAULT_SITE_SETTINGS},
        is_active=True,
    )
    return await site_repo().add(record)


async def update_site(ctx: TenantContext, site_id: str, data: dict[str, Any]) -> SiteRecord:
    site = await site_repo().get(site_id)
    if not site:
        raise NotFoundError("Site not found")
    ctx.ensure_org(site.organization_id)
    ctx.ensure_site(site.id)
    for key, value in data.items():
        setattr(site, key, value)
    return await site_repo().save(site)


async def get_site_or_404(site_id: str) -> SiteRecord:
    site = await site_repo().get(site_id)
    if not site:
        raise NotFoundError("Site not found")
    return site


# --- Gates ------------------------------------------------------------------------


async def list_gates(ctx: TenantContext, *, site_id: str | None = None) -> list[GateRecord]:
    if site_id:
        ctx.ensure_site(site_id)
    org_id = None if ctx.is_super else ctx.organization_id
    return await gate_repo().list_for_tenant(
        organization_id=org_id,
        site_ids=ctx.site_ids or None,
        site_id=site_id,
    )


async def create_gate(ctx: TenantContext, *, site_id: str, name: str, mode: str) -> GateRecord:
    site = await get_site_or_404(site_id)
    ctx.ensure_org(site.organization_id)
    ctx.ensure_site(site.id)
    record = GateRecord(
        id=str(uuid4()),
        organization_id=site.organization_id,
        site_id=site.id,
        name=name,
        mode=_enum_str(mode) or "MIXED",
        is_active=True,
    )
    return await gate_repo().add(record)


async def update_gate(ctx: TenantContext, gate_id: str, data: dict[str, Any]) -> GateRecord:
    gate = await gate_repo().get(gate_id)
    if not gate:
        raise NotFoundError("Gate not found")
    ctx.ensure_org(gate.organization_id)
    ctx.ensure_site(gate.site_id)
    if "mode" in data and data["mode"] is not None:
        data = {**data, "mode": _enum_str(data["mode"])}
    for key, value in data.items():
        setattr(gate, key, value)
    return await gate_repo().save(gate)


async def delete_gate(ctx: TenantContext, gate_id: str) -> None:
    gate = await gate_repo().get(gate_id)
    if not gate:
        raise NotFoundError("Gate not found")
    ctx.ensure_org(gate.organization_id)
    await gate_repo().delete(gate_id)


# --- Cameras ----------------------------------------------------------------------


def camera_record_to_out(
    camera: CameraRecord,
    *,
    site_name: str | None = None,
    gate_name: str | None = None,
) -> dict[str, Any]:
    return {
        "id": camera.id,
        "organization_id": camera.organization_id,
        "site_id": camera.site_id,
        "gate_id": camera.gate_id or "",
        "name": camera.name,
        "camera_code": camera.camera_code,
        "direction": camera.direction,
        "onvif_ip": camera.onvif_ip,
        "stream_type": camera.stream_type,
        "resolution": camera.resolution,
        "enabled": camera.enabled,
        "streaming": camera.streaming,
        "status": camera.status,
        "last_heartbeat": camera.last_heartbeat,
        "fps": camera.fps,
        "connection_error": camera.connection_error,
        "last_frame_at": camera.last_frame_at,
        "retry_count": camera.retry_count,
        "credentials_configured": bool(camera.username_encrypted or camera.password_encrypted),
        "rtsp_configured": bool(camera.rtsp_url_encrypted),
        "site_name": site_name,
        "gate_name": gate_name,
        "source_type": camera.source_type or "RTSP",
        "nvr_id": camera.nvr_id,
        "channel": camera.channel,
        "anpr_enabled": camera.anpr_enabled,
        "gateway_id": camera.gateway_id,
        "last_seen": camera.last_seen or camera.last_heartbeat,
    }


async def _bind_nvr_gateway(site_id: str, nvr_id: str | None, gateway_id: str | None) -> None:
    if nvr_id:
        nvr = await nvr_repo().get(nvr_id)
        if nvr is None or nvr.site_id != site_id:
            raise ValidationAppError("NVR does not belong to the selected site")
    if gateway_id:
        gw = await gateway_repo().get(gateway_id)
        if gw is None or gw.site_id != site_id:
            raise ValidationAppError("Gateway does not belong to the selected site")


async def list_cameras(ctx: TenantContext, *, site_id: str | None = None) -> list[dict[str, Any]]:
    if site_id:
        ctx.ensure_site(site_id)
    org_id = None if ctx.is_super else ctx.organization_id
    rows = await camera_repo().list_for_tenant(
        organization_id=org_id,
        site_ids=ctx.site_ids or None,
        site_id=site_id,
    )
    return [await _camera_out_enriched(c) for c in rows]


async def camera_out_enriched(camera: CameraRecord) -> dict[str, Any]:
    site = await site_repo().get(camera.site_id)
    gate = await gate_repo().get(camera.gate_id) if camera.gate_id else None
    return camera_record_to_out(
        camera,
        site_name=site.name if site else None,
        gate_name=gate.name if gate else None,
    )


async def _camera_out_enriched(camera: CameraRecord) -> dict[str, Any]:
    return await camera_out_enriched(camera)


async def get_camera(ctx: TenantContext, camera_id: str) -> CameraRecord:
    camera = await camera_repo().get(camera_id)
    if not camera:
        raise NotFoundError("Camera not found")
    ctx.ensure_org(camera.organization_id)
    ctx.ensure_site(camera.site_id)
    return camera


async def get_camera_out(ctx: TenantContext, camera_id: str) -> dict[str, Any]:
    camera = await get_camera(ctx, camera_id)
    return await _camera_out_enriched(camera)


async def create_camera(ctx: TenantContext, body: Any) -> dict[str, Any]:
    site = await get_site_or_404(body.site_id)
    gate = await gate_repo().get(body.gate_id)
    if not gate:
        raise NotFoundError("Site or gate not found")
    if gate.site_id != site.id:
        raise ValidationAppError("Gate does not belong to the selected site")
    ctx.ensure_org(site.organization_id)
    ctx.ensure_site(site.id)
    await _bind_nvr_gateway(site.id, body.nvr_id, body.gateway_id)
    user_enc = encrypt_optional(body.username)
    pass_enc = encrypt_optional(body.password)
    rtsp_enc = encrypt_optional(body.rtsp_url)
    record = CameraRecord(
        id=str(uuid4()),
        organization_id=site.organization_id,
        site_id=site.id,
        gate_id=gate.id,
        name=body.name,
        camera_code=body.camera_code,
        direction=_enum_str(body.direction),
        source_type=_enum_str(body.source_type) or "RTSP",
        nvr_id=body.nvr_id,
        channel=body.channel,
        gateway_id=body.gateway_id,
        stream_type=_enum_str(body.stream_type) or "RTSP",
        status="UNKNOWN",
        enabled=body.enabled,
        anpr_enabled=body.anpr_enabled,
        resolution=body.resolution or "1920x1080",
        onvif_ip=body.onvif_ip,
        rtsp_url_encrypted=rtsp_enc,
        username_encrypted=user_enc,
        password_encrypted=pass_enc,
        has_credentials=bool(user_enc or pass_enc),
    )
    saved = await camera_repo().add(record)
    return await _camera_out_enriched(saved)


async def update_camera(ctx: TenantContext, camera_id: str, body: Any) -> dict[str, Any]:
    camera = await get_camera(ctx, camera_id)
    data = body.model_dump(exclude_unset=True)
    if "rtsp_url" in data:
        camera.rtsp_url_encrypted = encrypt_optional(data.pop("rtsp_url"))
    if "username" in data:
        camera.username_encrypted = encrypt_optional(data.pop("username"))
    if "password" in data:
        camera.password_encrypted = encrypt_optional(data.pop("password"))
    camera.has_credentials = bool(camera.username_encrypted or camera.password_encrypted)
    nvr_id = data.get("nvr_id", camera.nvr_id)
    gateway_id = data.get("gateway_id", camera.gateway_id)
    await _bind_nvr_gateway(camera.site_id, nvr_id, gateway_id)
    for key, value in data.items():
        if key in {"direction", "source_type", "stream_type"} and value is not None:
            setattr(camera, key, _enum_str(value))
        else:
            setattr(camera, key, value)
    saved = await camera_repo().save(camera)
    return await _camera_out_enriched(saved)


async def delete_camera(ctx: TenantContext, camera_id: str) -> None:
    await get_camera(ctx, camera_id)
    await camera_repo().delete(camera_id)


async def enable_camera(ctx: TenantContext, camera_id: str, enabled: bool) -> dict[str, Any]:
    camera = await get_camera(ctx, camera_id)
    camera.enabled = enabled
    saved = await camera_repo().save(camera)
    await hub.publish(camera.organization_id, "camera.status", {"id": camera.id, "enabled": enabled})
    return await _camera_out_enriched(saved)


# --- Vehicles ---------------------------------------------------------------------


async def list_vehicles(ctx: TenantContext, *, q: str | None = None) -> list[VehicleRecord]:
    if not ctx.organization_id and not ctx.is_super:
        return []
    org_id = ctx.organization_id
    if ctx.is_super and not org_id:
        # Super without org filter: limited empty — require org context for plate search.
        return []
    assert org_id is not None
    rows = await vehicle_repo().list_for_org(org_id, limit=100)
    if q:
        compact = normalize_plate(q).normalized
        rows = [v for v in rows if compact in v.plate_normalized]
    return rows


async def get_vehicle_by_plate(ctx: TenantContext, plate: str) -> VehicleRecord:
    compact = normalize_plate(plate).normalized
    if not ctx.organization_id:
        raise NotFoundError("Vehicle not found")
    vehicle = await vehicle_repo().get_by_plate(ctx.organization_id, compact)
    if not vehicle:
        raise NotFoundError("Vehicle not found")
    ctx.ensure_org(vehicle.organization_id)
    return vehicle


async def update_vehicle(ctx: TenantContext, plate: str, data: dict[str, Any]) -> VehicleRecord:
    vehicle = await get_vehicle_by_plate(ctx, plate)
    for key, value in data.items():
        if value is not None:
            setattr(vehicle, key, value)
    return await vehicle_repo().save(vehicle)


# --- Events -----------------------------------------------------------------------


def serialize_event_record(
    event: AnprEventRecord,
    camera: CameraRecord | None = None,
    site: SiteRecord | None = None,
    *,
    gate_name: str | None = None,
) -> dict[str, Any]:
    return {
        "id": event.id,
        "organization_id": event.organization_id,
        "site_id": event.site_id,
        "gate_id": event.gate_id,
        "camera_id": event.camera_id,
        "vehicle_id": event.vehicle_id,
        "visit_id": event.visit_id,
        "direction": event.direction,
        "plate_text": event.plate_text,
        "raw_ocr_text": event.raw_ocr_text,
        "plate_normalized": event.plate_normalized,
        "ocr_confidence": event.ocr_confidence,
        "plate_detection_confidence": event.plate_detection_confidence,
        "vehicle_detection_confidence": event.vehicle_detection_confidence,
        "timestamp": event.timestamp,
        "local_timestamp": event.local_timestamp,
        "snapshot_path": event.snapshot_storage_key,
        "plate_crop_path": event.plate_crop_storage_key,
        "vehicle_crop_path": event.vehicle_crop_storage_key,
        "processing_duration_ms": event.processing_duration_ms,
        "source_type": event.source_type,
        "sync_status": event.sync_status,
        "classification": event.classification,
        "notes": event.notes,
        "camera_name": camera.name if camera else None,
        "gate_name": gate_name,
        "site_name": site.name if site else None,
        "duplicate_suppressed": False,
    }


async def _find_duplicate_fs(
    *,
    organization_id: str,
    site_id: str,
    camera_id: str,
    gate_id: str,
    plate_normalized: str,
    timestamp: datetime,
    window_seconds: int,
) -> AnprEventRecord | None:
    since = timestamp - timedelta(seconds=window_seconds)
    rows = await anpr_event_repo().list_for_tenant(
        organization_id=organization_id,
        site_id=site_id,
        limit=50,
    )
    for ev in rows:
        if (
            ev.camera_id == camera_id
            and ev.gate_id == gate_id
            and ev.plate_normalized == plate_normalized
            and ev.timestamp >= since
            and ev.timestamp <= timestamp
        ):
            return ev
    return None


async def _find_open_visit(vehicle_id: str, site_id: str, organization_id: str) -> VisitRecord | None:
    visits = await visit_repo().list_for_tenant(
        organization_id=organization_id,
        site_id=site_id,
        limit=50,
    )
    for v in visits:
        if v.vehicle_id == vehicle_id and v.status == str(VisitStatus.CURRENTLY_INSIDE):
            return v
    return None


async def _apply_visit_match_fs(
    event: AnprEventRecord,
    vehicle: VehicleRecord,
) -> VisitRecord:
    direction = event.direction
    if direction == str(Direction.EXIT) or direction == Direction.EXIT:
        existing = await _find_open_visit(vehicle.id, event.site_id, event.organization_id)
        if existing:
            existing.exit_event_id = event.id
            existing.exit_at = event.timestamp
            existing.status = str(VisitStatus.COMPLETED)
            if existing.entry_at and event.timestamp:
                entry_at = existing.entry_at
                exit_at = event.timestamp
                if entry_at.tzinfo is None and exit_at.tzinfo is not None:
                    entry_at = entry_at.replace(tzinfo=exit_at.tzinfo)
                elif exit_at.tzinfo is None and entry_at.tzinfo is not None:
                    exit_at = exit_at.replace(tzinfo=entry_at.tzinfo)
                existing.duration_seconds = int((exit_at - entry_at).total_seconds())
            event.visit_id = existing.id
            vehicle.currently_inside = False
            vehicle.last_seen = event.timestamp
            await visit_repo().save(existing)
            await vehicle_repo().save(vehicle)
            return existing
        visit = VisitRecord(
            id=str(uuid4()),
            organization_id=event.organization_id,
            site_id=event.site_id,
            vehicle_id=vehicle.id,
            plate_normalized=event.plate_normalized,
            exit_event_id=event.id,
            exit_at=event.timestamp,
            gate_id=event.gate_id,
            status=str(VisitStatus.EXIT_WITHOUT_MATCH),
        )
        await visit_repo().add(visit)
        event.visit_id = visit.id
        vehicle.currently_inside = False
        vehicle.last_seen = event.timestamp
        await vehicle_repo().save(vehicle)
        return visit

    # ENTRY (or BOTH / default)
    existing = await _find_open_visit(vehicle.id, event.site_id, event.organization_id)
    if existing:
        event.visit_id = existing.id
        vehicle.last_seen = event.timestamp
        vehicle.currently_inside = True
        await vehicle_repo().save(vehicle)
        return existing
    visit = VisitRecord(
        id=str(uuid4()),
        organization_id=event.organization_id,
        site_id=event.site_id,
        vehicle_id=vehicle.id,
        plate_normalized=event.plate_normalized,
        entry_event_id=event.id,
        entry_at=event.timestamp,
        gate_id=event.gate_id,
        status=str(VisitStatus.CURRENTLY_INSIDE),
    )
    await visit_repo().add(visit)
    event.visit_id = visit.id
    vehicle.currently_inside = True
    vehicle.last_seen = event.timestamp
    vehicle.total_visits += 1
    await vehicle_repo().save(vehicle)
    return visit


async def ingest_event_fs(
    *,
    camera: CameraRecord,
    site: SiteRecord,
    plate_text: str,
    raw_ocr_text: str | None = None,
    direction: Direction | str | None = None,
    ocr_confidence: float,
    plate_detection_confidence: float = 0.0,
    vehicle_detection_confidence: float = 0.0,
    timestamp: datetime | None = None,
    local_timestamp: datetime | None = None,
    processing_duration_ms: int = 0,
    source_type: SourceType | str = SourceType.EDGE,
    event_id: str | None = None,
    snapshot_bytes: bytes | None = None,
    plate_crop_bytes: bytes | None = None,
    vehicle_crop_bytes: bytes | None = None,
    force: bool = False,
) -> tuple[AnprEventRecord | None, str]:
    """Mirror ingest_event using Firestore repos + object storage for evidence bytes."""
    eid = event_id or str(uuid4())
    existing = await anpr_event_repo().get(eid)
    if existing is not None:
        return existing, "idempotent"

    cfg = _site_settings(site)
    min_conf = float(cfg.get("min_confidence", 0.7))
    if not force and ocr_confidence < min_conf:
        return None, "low_confidence"

    norm = normalize_plate(plate_text, confusable_substitution=bool(cfg.get("confusable_substitution", False)))
    ts = timestamp or datetime.now(tz=ZoneInfo("UTC"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ZoneInfo("UTC"))
    tz = ZoneInfo(site.timezone or "Asia/Kolkata")
    local_ts = local_timestamp or ts.astimezone(tz)

    cam_dir = camera.direction
    if direction is None:
        event_direction = Direction.ENTRY if cam_dir == str(Direction.BOTH) or cam_dir == "BOTH" else Direction(cam_dir)
    else:
        event_direction = direction if isinstance(direction, Direction) else Direction(str(direction))

    gate_id = camera.gate_id or ""
    window = max(int(cfg.get("duplicate_window_seconds", 30)), int(cfg.get("event_cooldown_seconds", 120)))
    dup = await _find_duplicate_fs(
        organization_id=camera.organization_id,
        site_id=site.id,
        camera_id=camera.id,
        gate_id=gate_id,
        plate_normalized=norm.normalized,
        timestamp=ts,
        window_seconds=window,
    )
    if dup is not None and not force:
        return dup, "duplicate"

    vehicle = await vehicle_repo().get_by_plate(camera.organization_id, norm.normalized)
    if vehicle is None:
        vehicle = VehicleRecord(
            id=str(uuid4()),
            organization_id=camera.organization_id,
            plate_normalized=norm.normalized,
            first_seen=ts,
            last_seen=ts,
            total_visits=0,
            currently_inside=False,
        )
        await vehicle_repo().add(vehicle)
    else:
        vehicle.last_seen = ts
        await vehicle_repo().save(vehicle)

    storage = get_storage()
    keys = event_storage_keys(camera.organization_id, eid)
    snapshot_key = plate_key = vehicle_key = None
    if snapshot_bytes:
        snapshot_key = storage.save(keys["snapshot"], snapshot_bytes, "image/jpeg")
    if plate_crop_bytes:
        plate_key = storage.save(keys["plate_crop"], plate_crop_bytes, "image/jpeg")
    if vehicle_crop_bytes:
        vehicle_key = storage.save(keys["vehicle_crop"], vehicle_crop_bytes, "image/jpeg")

    event = AnprEventRecord(
        id=eid,
        organization_id=camera.organization_id,
        site_id=camera.site_id,
        gate_id=gate_id,
        camera_id=camera.id,
        vehicle_id=vehicle.id,
        direction=_enum_str(event_direction),
        plate_text=plate_text,
        raw_ocr_text=raw_ocr_text or plate_text,
        plate_normalized=norm.normalized,
        ocr_confidence=ocr_confidence,
        plate_detection_confidence=plate_detection_confidence,
        vehicle_detection_confidence=vehicle_detection_confidence,
        timestamp=ts,
        local_timestamp=local_ts,
        snapshot_storage_key=snapshot_key,
        plate_crop_storage_key=plate_key,
        vehicle_crop_storage_key=vehicle_key,
        processing_duration_ms=processing_duration_ms,
        source_type=_enum_str(source_type) or str(SourceType.EDGE),
        sync_status=str(SyncStatus.SYNCED),
    )
    await _apply_visit_match_fs(event, vehicle)
    await anpr_event_repo().add(event)

    await hub.publish(
        camera.organization_id,
        "anpr.event",
        {"id": event.id, "plate": event.plate_normalized, "direction": event.direction},
    )
    return event, "created"


async def list_events(
    ctx: TenantContext,
    *,
    site_id: str | None = None,
    organization_id: str | None = None,
    plate: str | None = None,
    direction: Direction | None = None,
    camera_id: str | None = None,
    gate_id: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[dict[str, Any]], int]:
    org_id = organization_id or ctx.organization_id
    if organization_id:
        ctx.ensure_org(organization_id)
    if site_id:
        ctx.ensure_site(site_id)
    if not org_id and not ctx.is_super:
        return [], 0
    # Super without org: limited empty list for cost control unless org provided
    if not org_id:
        return [], 0

    fetch = min(page * page_size, 200)
    rows = await anpr_event_repo().list_for_tenant(
        organization_id=org_id,
        site_ids=ctx.site_ids or None,
        site_id=site_id,
        limit=fetch,
    )
    if plate:
        compact = normalize_plate(plate).normalized
        rows = [e for e in rows if compact in e.plate_normalized]
    if direction:
        d = _enum_str(direction)
        rows = [e for e in rows if e.direction == d]
    if camera_id:
        rows = [e for e in rows if e.camera_id == camera_id]
    if gate_id:
        rows = [e for e in rows if e.gate_id == gate_id]

    total = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start : start + page_size]
    items: list[dict[str, Any]] = []
    for ev in page_rows:
        cam = await camera_repo().get(ev.camera_id)
        site = await site_repo().get(ev.site_id)
        gate = await gate_repo().get(ev.gate_id) if ev.gate_id else None
        items.append(serialize_event_record(ev, cam, site, gate_name=gate.name if gate else None))
    return items, total


async def get_event(ctx: TenantContext, event_id: str) -> dict[str, Any]:
    event = await anpr_event_repo().get(event_id)
    if not event:
        raise NotFoundError("Event not found")
    ctx.ensure_org(event.organization_id)
    ctx.ensure_site(event.site_id)
    cam = await camera_repo().get(event.camera_id)
    site = await site_repo().get(event.site_id)
    gate = await gate_repo().get(event.gate_id) if event.gate_id else None
    return serialize_event_record(event, cam, site, gate_name=gate.name if gate else None)


async def correct_event(ctx: TenantContext, event_id: str, plate_text: str) -> dict[str, Any]:
    event = await anpr_event_repo().get(event_id)
    if not event:
        raise NotFoundError("Event not found")
    ctx.ensure_org(event.organization_id)
    event.plate_text = plate_text
    event.plate_normalized = normalize_plate(plate_text).normalized
    await anpr_event_repo().save(event)
    return await get_event(ctx, event_id)


async def classify_event(
    ctx: TenantContext,
    event_id: str,
    *,
    classification: str,
    notes: str | None,
) -> dict[str, Any]:
    event = await anpr_event_repo().get(event_id)
    if not event:
        raise NotFoundError("Event not found")
    ctx.ensure_org(event.organization_id)
    event.classification = classification
    event.notes = notes
    await anpr_event_repo().save(event)
    return await get_event(ctx, event_id)


async def delete_event(ctx: TenantContext, event_id: str) -> None:
    event = await anpr_event_repo().get(event_id)
    if not event:
        raise NotFoundError("Event not found")
    ctx.ensure_org(event.organization_id)
    await anpr_event_repo().delete(event_id)


# --- Edge agents ------------------------------------------------------------------


async def register_edge_agent_fs(*, site_id: str, name: str, agent_key: str) -> EdgeAgentRecord:
    site = await get_site_or_404(site_id)
    record = EdgeAgentRecord(
        id=str(uuid4()),
        organization_id=site.organization_id,
        site_id=site.id,
        name=name,
        agent_key_hash=hash_password(agent_key),
        status="CONNECTED",
        last_seen=datetime.now(UTC),
        is_active=True,
    )
    return await edge_agent_repo().add(record)


async def heartbeat_edge_agent_fs(
    agent: EdgeAgentRecord,
    *,
    cpu_usage: float | None,
    memory_usage: float | None,
    queue_size: int,
    camera_statuses: list[dict[str, Any]],
) -> EdgeAgentRecord:
    agent.status = "CONNECTED"
    agent.last_seen = datetime.now(UTC)
    agent.cpu_usage = cpu_usage
    agent.memory_usage = memory_usage
    agent.queue_size = queue_size
    await edge_agent_repo().save(agent)

    for item in camera_statuses:
        cam_id = item.get("id")
        if not cam_id:
            continue
        camera = await camera_repo().get(str(cam_id))
        if camera is None or camera.site_id != agent.site_id:
            continue
        if "status" in item and item["status"]:
            camera.status = str(item["status"])
        if "fps" in item:
            camera.fps = item["fps"]
        if "retry_count" in item:
            camera.retry_count = int(item["retry_count"] or 0)
        if "connection_error" in item:
            err = item["connection_error"]
            camera.connection_error = str(err)[:500] if err else None
        if "last_frame_at" in item and item["last_frame_at"]:
            try:
                camera.last_frame_at = datetime.fromisoformat(item["last_frame_at"])
            except ValueError:
                pass
        camera.last_heartbeat = datetime.now(UTC)
        camera.last_seen = camera.last_heartbeat
        await camera_repo().save(camera)
        await hub.publish(
            agent.organization_id,
            "camera.status",
            {
                "id": camera.id,
                "status": camera.status,
                "fps": camera.fps,
                "streaming": camera.streaming,
            },
        )
    await hub.publish(agent.organization_id, "edge.status", {"id": agent.id, "status": agent.status})
    return agent


# --- Users ------------------------------------------------------------------------


async def list_users(ctx: TenantContext) -> list[UserProfileRecord]:
    if ctx.is_super:
        # No global list-all on users repo — return empty for platform unless org scoped.
        if ctx.organization_id:
            return await user_profile_repo().list_for_org(ctx.organization_id)
        return []
    if not ctx.organization_id:
        return []
    return await user_profile_repo().list_for_org(ctx.organization_id)


async def create_user_profile(
    ctx: TenantContext,
    *,
    email: str,
    full_name: str,
    role: str,
    organization_id: str | None,
    site_ids: list[str],
) -> UserProfileRecord:
    existing = await user_profile_repo().get_by_email(email.lower())
    if existing:
        raise ConflictError("Email already registered")
    record = UserProfileRecord(
        id=str(uuid4()),
        email=email.lower(),
        full_name=full_name,
        role=_enum_str(role),
        organization_id=organization_id,
        site_ids=list(site_ids or []),
        is_active=True,
        auth_provider="firebase",
    )
    return await user_profile_repo().add(record)


# --- Dashboard --------------------------------------------------------------------


async def dashboard_summary_fs(
    ctx: TenantContext,
    *,
    site_id: str | None = None,
) -> dict[str, Any]:
    tz_name = "Asia/Kolkata"
    if site_id:
        ctx.ensure_site(site_id)
        site = await site_repo().get(site_id)
        if site:
            ctx.ensure_org(site.organization_id)
            tz_name = site.timezone

    org_id = ctx.organization_id
    cameras: list[CameraRecord] = []
    events: list[AnprEventRecord] = []
    if org_id:
        cameras = await camera_repo().list_for_tenant(
            organization_id=org_id,
            site_ids=ctx.site_ids or None,
            site_id=site_id,
        )
        events = await anpr_event_repo().list_for_tenant(
            organization_id=org_id,
            site_ids=ctx.site_ids or None,
            site_id=site_id,
            limit=50,
        )

    today = datetime.now(ZoneInfo(tz_name)).date()
    entries = exits = detections = 0
    for ev in events:
        local = ev.local_timestamp or ev.timestamp
        if local.tzinfo is None:
            local = local.replace(tzinfo=ZoneInfo("UTC"))
        try:
            local_day = local.astimezone(ZoneInfo(tz_name)).date()
        except Exception:
            local_day = local.date()
        if local_day != today:
            continue
        detections += 1
        if ev.direction == str(Direction.ENTRY):
            entries += 1
        elif ev.direction == str(Direction.EXIT):
            exits += 1

    active = sum(1 for c in cameras if c.status == "ONLINE" and c.enabled)
    offline = sum(1 for c in cameras if c.status != "ONLINE")
    recent_out: list[dict[str, Any]] = []
    for ev in events[:12]:
        cam = next((c for c in cameras if c.id == ev.camera_id), None)
        site = await site_repo().get(ev.site_id) if ev.site_id else None
        recent_out.append(serialize_event_record(ev, cam, site))

    return {
        "entries_today": entries,
        "exits_today": exits,
        "currently_inside": 0,
        "detections_today": detections,
        "cameras_active": active,
        "cameras_offline": offline,
        "cameras_total": len(cameras),
        "timezone": tz_name,
        "recent_events": recent_out,
    }


async def ensure_site_connectivity_defaults(site: SiteRecord) -> SiteConnectivityRecord:
    conn = await site_connectivity_repo().get(site.id)
    if conn:
        return conn
    record = SiteConnectivityRecord(
        site_id=site.id,
        organization_id=site.organization_id,
        connectivity_mode=site.connectivity_mode,
        anpr_deployment_mode=site.anpr_deployment_mode,
        primary_gateway_id=site.primary_gateway_id,
    )
    return await site_connectivity_repo().save(record)
