from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import client_ip, get_db, get_tenant, require_permission
from app.core.exceptions import NotFoundError, ValidationAppError
from app.core.rbac import Permission
from app.core.runtime import is_firestore
from app.models.camera import Camera
from app.models.enums import AuditAction, Direction, SourceType
from app.models.site import Site
from app.repositories import camera_repo, site_repo
from app.schemas.event import EventOut, MockEventRequest
from app.services import firestore_domain as fs
from app.services.audit import write_audit
from app.services.event import ingest_event, serialize_event
from app.services.tenant import TenantContext

router = APIRouter(prefix="/mock", tags=["mock"])


def _validate_direction(camera_direction: Direction | str, direction: Direction | None) -> Direction:
    cam_dir = camera_direction if isinstance(camera_direction, Direction) else Direction(str(camera_direction))
    resolved = direction
    if resolved is None:
        if cam_dir == Direction.BOTH:
            resolved = Direction.ENTRY
        else:
            resolved = cam_dir
    if cam_dir != Direction.BOTH and resolved != cam_dir:
        raise ValidationAppError(
            f"Direction {resolved} does not match camera configuration ({cam_dir})",
            details={"camera_direction": cam_dir, "requested_direction": resolved},
        )
    return resolved


async def _load_camera(db: AsyncSession, camera_id: str) -> Camera:
    stmt = select(Camera).options(selectinload(Camera.gate)).where(Camera.id == camera_id)
    camera = (await db.execute(stmt)).scalar_one_or_none()
    if not camera:
        raise NotFoundError("Camera not found")
    return camera


@router.post("/events", response_model=EventOut)
async def mock_event(
    body: MockEventRequest,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.MOCK_WRITE)),
) -> EventOut:
    if is_firestore():
        camera = await camera_repo().get(body.camera_id)
        if not camera:
            raise NotFoundError("Camera not found")
        ctx.ensure_org(camera.organization_id)
        ctx.ensure_site(camera.site_id)
        site = await site_repo().get(camera.site_id)
        if not site:
            raise NotFoundError("Site not found")
        direction = _validate_direction(camera.direction, body.direction)
        event, reason = await fs.ingest_event_fs(
            camera=camera,
            site=site,
            plate_text=body.plate_text,
            direction=direction,
            ocr_confidence=body.ocr_confidence,
            plate_detection_confidence=body.plate_detection_confidence,
            vehicle_detection_confidence=body.vehicle_detection_confidence,
            source_type=body.source_type or SourceType.MOCK,
            event_id=body.event_id,
            force=True,
        )
        await write_audit(
            db,
            action=AuditAction.MOCK_EVENT,
            user_id=ctx.user.id,
            organization_id=camera.organization_id,
            ip=client_ip(request),
            target_type="anpr_event",
            target_id=event.id if event else None,
            extra={"reason": reason},
        )
        if event is None:
            raise NotFoundError("Event was not created")
        return EventOut.model_validate(
            fs.serialize_event_record(event, camera, site) | {"duplicate_suppressed": reason == "duplicate"}
        )

    assert db is not None
    camera = await _load_camera(db, body.camera_id)
    ctx.ensure_org(camera.organization_id)
    ctx.ensure_site(camera.site_id)
    site = await db.get(Site, camera.site_id)
    if not site:
        raise NotFoundError("Site not found")
    direction = _validate_direction(camera.direction, body.direction)
    event, reason = await ingest_event(
        db,
        camera=camera,
        site=site,
        plate_text=body.plate_text,
        direction=direction,
        ocr_confidence=body.ocr_confidence,
        plate_detection_confidence=body.plate_detection_confidence,
        vehicle_detection_confidence=body.vehicle_detection_confidence,
        source_type=body.source_type or SourceType.MOCK,
        event_id=body.event_id,
        force=True,
    )
    await write_audit(
        db,
        action=AuditAction.MOCK_EVENT,
        user_id=ctx.user.id,
        organization_id=camera.organization_id,
        ip=client_ip(request),
        target_type="anpr_event",
        target_id=event.id if event else None,
        extra={"reason": reason},
    )
    await db.commit()
    if event is None:
        raise NotFoundError("Event was not created")
    await db.refresh(event)
    return EventOut.model_validate(
        serialize_event(event, camera, site) | {"duplicate_suppressed": reason == "duplicate"}
    )


@router.post("/upload", response_model=EventOut)
async def mock_upload(
    request: Request,
    camera_id: str = Form(...),
    plate_text: str = Form(...),
    direction: Direction | None = Form(default=None),
    file: UploadFile = File(...),
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.MOCK_WRITE)),
) -> EventOut:
    if is_firestore():
        camera = await camera_repo().get(camera_id)
        if not camera:
            raise NotFoundError("Camera not found")
        ctx.ensure_org(camera.organization_id)
        ctx.ensure_site(camera.site_id)
        site = await site_repo().get(camera.site_id)
        if not site:
            raise NotFoundError("Site not found")
        resolved = _validate_direction(camera.direction, direction)
        data = await file.read()
        event, _reason = await fs.ingest_event_fs(
            camera=camera,
            site=site,
            plate_text=plate_text,
            direction=resolved,
            ocr_confidence=0.9,
            source_type=SourceType.MOCK,
            snapshot_bytes=data,
            timestamp=datetime.now(UTC),
            force=True,
        )
        await write_audit(
            db,
            action=AuditAction.MOCK_EVENT,
            user_id=ctx.user.id,
            organization_id=camera.organization_id,
            ip=client_ip(request),
            target_type="anpr_event",
            target_id=event.id if event else None,
            extra={"filename": file.filename},
        )
        if event is None:
            raise NotFoundError("Event was not created")
        return EventOut.model_validate(fs.serialize_event_record(event, camera, site))

    assert db is not None
    camera = await _load_camera(db, camera_id)
    ctx.ensure_org(camera.organization_id)
    ctx.ensure_site(camera.site_id)
    site = await db.get(Site, camera.site_id)
    if not site:
        raise NotFoundError("Site not found")
    resolved = _validate_direction(camera.direction, direction)
    data = await file.read()
    event, _reason = await ingest_event(
        db,
        camera=camera,
        site=site,
        plate_text=plate_text,
        direction=resolved,
        ocr_confidence=0.9,
        source_type=SourceType.MOCK,
        snapshot_bytes=data,
        timestamp=datetime.now(UTC),
        force=True,
    )
    await write_audit(
        db,
        action=AuditAction.MOCK_EVENT,
        user_id=ctx.user.id,
        organization_id=camera.organization_id,
        ip=client_ip(request),
        target_type="anpr_event",
        target_id=event.id if event else None,
        extra={"filename": file.filename},
    )
    await db.commit()
    if event is None:
        raise NotFoundError("Event was not created")
    return EventOut.model_validate(serialize_event(event, camera, site))
