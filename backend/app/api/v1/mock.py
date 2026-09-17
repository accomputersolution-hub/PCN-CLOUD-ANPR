from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import client_ip, get_db, get_tenant, require_permission
from app.core.exceptions import NotFoundError, ValidationAppError
from app.core.rbac import Permission
from app.models.camera import Camera
from app.models.enums import AuditAction, Direction, SourceType
from app.models.site import Site
from app.schemas.event import EventOut, MockEventRequest
from app.services.audit import write_audit
from app.services.event import ingest_event, serialize_event
from app.services.tenant import TenantContext

router = APIRouter(prefix="/mock", tags=["mock"])


def _validate_direction(camera: Camera, direction: Direction | None) -> Direction:
    """Resolve and validate direction against camera configuration."""
    resolved = direction
    if resolved is None:
        if camera.direction == Direction.BOTH:
            resolved = Direction.ENTRY
        else:
            resolved = camera.direction
    if camera.direction != Direction.BOTH and resolved != camera.direction:
        raise ValidationAppError(
            f"Direction {resolved} does not match camera configuration ({camera.direction})",
            details={"camera_direction": camera.direction, "requested_direction": resolved},
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
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.MOCK_WRITE)),
) -> EventOut:
    camera = await _load_camera(db, body.camera_id)
    ctx.ensure_org(camera.organization_id)
    ctx.ensure_site(camera.site_id)
    site = await db.get(Site, camera.site_id)
    if not site:
        raise NotFoundError("Site not found")
    direction = _validate_direction(camera, body.direction)
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
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.MOCK_WRITE)),
) -> EventOut:
    camera = await _load_camera(db, camera_id)
    ctx.ensure_org(camera.organization_id)
    ctx.ensure_site(camera.site_id)
    site = await db.get(Site, camera.site_id)
    if not site:
        raise NotFoundError("Site not found")
    resolved = _validate_direction(camera, direction)
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
