from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import inspect as sa_inspect, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.anpr_event import AnprEvent
from app.models.camera import Camera
from app.models.enums import Direction, SnapshotKind, SourceType, SyncStatus
from app.models.site import DEFAULT_SITE_SETTINGS, Site
from app.models.snapshot import Snapshot
from app.models.vehicle import Vehicle
from app.services.duplicate import find_duplicate_event
from app.services.plate import normalize_plate
from app.services.realtime import hub
from app.services.storage import get_storage
from app.services.visit import apply_visit_match
from app.firebase.cost_controls import event_storage_keys


def _settings(site: Site) -> dict[str, Any]:
    merged = {**DEFAULT_SITE_SETTINGS, **(site.settings or {})}
    return merged


def _loaded_attr(obj: object, name: str) -> Any | None:
    """Return a relationship attribute only if already loaded (async-safe)."""
    state = sa_inspect(obj)
    if name in state.unloaded:
        return None
    return getattr(obj, name, None)


def serialize_event(event: AnprEvent, camera: Camera | None = None, site: Site | None = None) -> dict[str, Any]:
    gate_name = None
    if camera is not None:
        gate = _loaded_attr(camera, "gate")
        if gate is not None:
            gate_name = gate.name
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
        "snapshot_path": event.snapshot_path,
        "plate_crop_path": event.plate_crop_path,
        "vehicle_crop_path": event.vehicle_crop_path,
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


async def ingest_event(
    db: AsyncSession,
    *,
    camera: Camera,
    site: Site,
    plate_text: str,
    raw_ocr_text: str | None = None,
    direction: Direction | None = None,
    ocr_confidence: float,
    plate_detection_confidence: float = 0.0,
    vehicle_detection_confidence: float = 0.0,
    timestamp: datetime | None = None,
    local_timestamp: datetime | None = None,
    processing_duration_ms: int = 0,
    source_type: SourceType = SourceType.EDGE,
    event_id: str | None = None,
    snapshot_bytes: bytes | None = None,
    plate_crop_bytes: bytes | None = None,
    vehicle_crop_bytes: bytes | None = None,
    force: bool = False,
) -> tuple[AnprEvent | None, str]:
    """Create an ANPR event with normalization, confidence, de-dupe, visit matching.

    Returns (event, reason). reason is 'created', 'duplicate', 'low_confidence', or 'idempotent'.
    """
    eid = event_id or str(uuid4())
    existing = await db.get(AnprEvent, eid)
    if existing is not None:
        return existing, "idempotent"

    cfg = _settings(site)
    min_conf = float(cfg.get("min_confidence", 0.7))
    if not force and ocr_confidence < min_conf:
        return None, "low_confidence"

    norm = normalize_plate(plate_text, confusable_substitution=bool(cfg.get("confusable_substitution", False)))
    ts = timestamp or datetime.now(tz=ZoneInfo("UTC"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ZoneInfo("UTC"))
    tz = ZoneInfo(site.timezone or "Asia/Kolkata")
    local_ts = local_timestamp or ts.astimezone(tz)
    event_direction = direction or (camera.direction if camera.direction != Direction.BOTH else Direction.ENTRY)

    dup = await find_duplicate_event(
        db,
        site_id=site.id,
        camera_id=camera.id,
        gate_id=camera.gate_id,
        plate_normalized=norm.normalized,
        timestamp=ts,
        duplicate_window_seconds=int(cfg.get("duplicate_window_seconds", 30)),
        event_cooldown_seconds=int(cfg.get("event_cooldown_seconds", 120)),
    )
    if dup is not None and not force:
        return dup, "duplicate"

    vehicle = (
        await db.execute(
            select(Vehicle).where(
                Vehicle.organization_id == camera.organization_id,
                Vehicle.plate_normalized == norm.normalized,
            )
        )
    ).scalar_one_or_none()
    if vehicle is None:
        vehicle = Vehicle(
            organization_id=camera.organization_id,
            plate_normalized=norm.normalized,
            first_seen=ts,
            last_seen=ts,
            total_visits=0,
            currently_inside=False,
        )
        db.add(vehicle)
        await db.flush()
    else:
        vehicle.last_seen = ts

    storage = get_storage()
    keys = event_storage_keys(camera.organization_id, eid)
    snapshot_key = plate_key = vehicle_key = None
    if snapshot_bytes:
        snapshot_key = storage.save(keys["snapshot"], snapshot_bytes, "image/jpeg")
    if plate_crop_bytes:
        plate_key = storage.save(keys["plate_crop"], plate_crop_bytes, "image/jpeg")
    if vehicle_crop_bytes:
        vehicle_key = storage.save(keys["vehicle_crop"], vehicle_crop_bytes, "image/jpeg")

    event = AnprEvent(
        id=eid,
        organization_id=camera.organization_id,
        site_id=camera.site_id,
        gate_id=camera.gate_id,
        camera_id=camera.id,
        vehicle_id=vehicle.id,
        direction=event_direction,
        plate_text=plate_text,
        raw_ocr_text=raw_ocr_text or plate_text,
        plate_normalized=norm.normalized,
        ocr_confidence=ocr_confidence,
        plate_detection_confidence=plate_detection_confidence,
        vehicle_detection_confidence=vehicle_detection_confidence,
        timestamp=ts,
        local_timestamp=local_ts,
        snapshot_path=snapshot_key,
        plate_crop_path=plate_key,
        vehicle_crop_path=vehicle_key,
        processing_duration_ms=processing_duration_ms,
        source_type=source_type,
        sync_status=SyncStatus.SYNCED,
    )
    db.add(event)
    await db.flush()

    if snapshot_key:
        db.add(Snapshot(organization_id=camera.organization_id, event_id=event.id, kind=SnapshotKind.FRAME, storage_key=snapshot_key, byte_size=len(snapshot_bytes or b"")))
    if plate_key:
        db.add(Snapshot(organization_id=camera.organization_id, event_id=event.id, kind=SnapshotKind.PLATE_CROP, storage_key=plate_key, byte_size=len(plate_crop_bytes or b"")))
    if vehicle_key:
        db.add(Snapshot(organization_id=camera.organization_id, event_id=event.id, kind=SnapshotKind.VEHICLE_CROP, storage_key=vehicle_key, byte_size=len(vehicle_crop_bytes or b"")))

    await apply_visit_match(db, event=event, vehicle=vehicle)
    await db.flush()

    await hub.publish(
        camera.organization_id,
        "anpr.event",
        {"id": event.id, "plate": event.plate_normalized, "direction": str(event.direction)},
    )
    return event, "created"


async def load_event(db: AsyncSession, event_id: str) -> AnprEvent | None:
    stmt = (
        select(AnprEvent)
        .options(selectinload(AnprEvent.camera), selectinload(AnprEvent.gate))
        .where(AnprEvent.id == event_id)
    )
    return (await db.execute(stmt)).scalar_one_or_none()
