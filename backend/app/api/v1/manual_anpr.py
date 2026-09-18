"""Manual ANPR capture API — analyze then confirm (no auto event creation)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, Field
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
from app.schemas.event import EventOut
from app.services import firestore_domain as fs
from app.services import manual_anpr as svc
from app.services.audit import write_audit
from app.services.event import ingest_event, serialize_event
from app.services.tenant import TenantContext

router = APIRouter(prefix="/manual-anpr", tags=["manual-anpr"])


class ManualAnprAnalyzeResponse(BaseModel):
    capture_id: str
    organization_id: str
    site_id: str
    camera_id: str
    vehicle_detected: bool
    plate_detected: bool
    detected_plate: str = ""
    raw_ocr: str = ""
    normalized_plate: str = ""
    ocr_confidence: float = 0.0
    plate_confidence: float = 0.0
    combined_confidence: float = 0.0
    matches_indian_pattern: bool = False
    ocr_confident: bool = False
    processing_ms: int = 0
    bbox: list[int] = Field(default_factory=list)
    plate_crop_jpeg_base64: str | None = None
    error: str | None = None
    event_created: bool = False
    plate_detector_mode: str = "opencv"
    plate_detector_used: str = "opencv"
    ai_detector_note: str | None = None
    plate_candidates: list[dict] = Field(default_factory=list)
    selected_bbox: list[int] = Field(default_factory=list)
    anpr_debug: dict = Field(default_factory=dict)


class ManualAnprConfirmRequest(BaseModel):
    capture_id: str
    plate_text: str = Field(min_length=1, max_length=32)
    direction: Direction
    ocr_confidence: float | None = Field(default=None, ge=0, le=1)
    plate_confidence: float | None = Field(default=None, ge=0, le=1)
    combined_confidence: float | None = Field(default=None, ge=0, le=1)


async def _load_camera_sql(db: AsyncSession, camera_id: str) -> Camera:
    stmt = select(Camera).options(selectinload(Camera.gate)).where(Camera.id == camera_id)
    camera = (await db.execute(stmt)).scalar_one_or_none()
    if not camera:
        raise NotFoundError("Camera not found")
    return camera


@router.post("/analyze", response_model=ManualAnprAnalyzeResponse)
async def analyze_manual_capture(
    camera_id: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.MANUAL_ANPR)),
) -> ManualAnprAnalyzeResponse:
    """Run existing ANPR engine on one image. Does not create an event."""
    import time

    api_t0 = time.time()
    _ = db
    t_read = time.time()
    data = await file.read()
    read_ms = (time.time() - t_read) * 1000.0
    print(f"[ANPR TIME] API) Upload file.read: {read_ms:.2f} ms ({read_ms / 1000.0:.4f} s)", flush=True)
    suffix = svc.validate_upload(filename=file.filename, content_type=file.content_type, data=data)

    t_auth = time.time()
    if is_firestore():
        camera = await camera_repo().get(camera_id)
        if not camera:
            raise NotFoundError("Camera not found")
        ctx.ensure_org(camera.organization_id)
        ctx.ensure_site(camera.site_id)
        org_id = camera.organization_id
        site_id = camera.site_id
    else:
        assert db is not None
        camera = await _load_camera_sql(db, camera_id)
        ctx.ensure_org(camera.organization_id)
        ctx.ensure_site(camera.site_id)
        org_id = camera.organization_id
        site_id = camera.site_id
    auth_ms = (time.time() - t_auth) * 1000.0
    print(f"[ANPR TIME] API) Camera/auth lookup: {auth_ms:.2f} ms ({auth_ms / 1000.0:.4f} s)", flush=True)

    t_anpr = time.time()
    result = svc.run_anpr_on_bytes(data, suffix=suffix)
    anpr_ms = (time.time() - t_anpr) * 1000.0
    print(f"[ANPR TIME] API) run_anpr_on_bytes: {anpr_ms:.2f} ms ({anpr_ms / 1000.0:.4f} s)", flush=True)
    plate_crop = result.pop("_plate_crop_bytes", None)
    plate = svc.best_plate(result) or {}
    analysis_meta = {
        "raw_ocr": plate.get("raw_text") or "",
        "normalized_plate": plate.get("normalized_text") or plate.get("normalized_plate") or "",
        "ocr_confidence": float(plate.get("ocr_confidence") or 0.0),
        "plate_confidence": float(plate.get("plate_confidence") or 0.0),
        "combined_confidence": float(plate.get("confidence") or 0.0),
        "matches_indian_pattern": bool(plate.get("matches_pattern")),
        "processing_ms": int(result.get("processing_ms") or 0),
    }
    t_pending = time.time()
    capture_id = svc.save_pending(
        organization_id=org_id,
        site_id=site_id,
        camera_id=camera_id,
        operator_user_id=ctx.user.id,
        snapshot_bytes=data,
        plate_crop_bytes=plate_crop if isinstance(plate_crop, (bytes, bytearray)) else None,
        analysis=analysis_meta,
    )
    pending_ms = (time.time() - t_pending) * 1000.0
    print(f"[ANPR TIME] API) save_pending: {pending_ms:.2f} ms ({pending_ms / 1000.0:.4f} s)", flush=True)
    t_resp = time.time()
    payload = svc.analysis_response(
        capture_id=capture_id,
        organization_id=org_id,
        site_id=site_id,
        camera_id=camera_id,
        result=result,
        plate_crop_bytes=plate_crop if isinstance(plate_crop, (bytes, bytearray)) else None,
    )
    result.pop("_anpr_debug", None)
    resp_ms = (time.time() - t_resp) * 1000.0
    total_ms = (time.time() - api_t0) * 1000.0
    print(f"[ANPR TIME] API) analysis_response build: {resp_ms:.2f} ms ({resp_ms / 1000.0:.4f} s)", flush=True)
    print("=" * 60, flush=True)
    print("[ANPR TIME] TOTAL API /manual-anpr/analyze delivery", flush=True)
    print("-" * 60, flush=True)
    print(f"  {'Upload file.read':40s} {read_ms:10.2f} ms", flush=True)
    print(f"  {'Camera/auth lookup':40s} {auth_ms:10.2f} ms", flush=True)
    print(f"  {'ANPR engine (run_anpr_on_bytes)':40s} {anpr_ms:10.2f} ms", flush=True)
    print(f"  {'save_pending':40s} {pending_ms:10.2f} ms", flush=True)
    print(f"  {'Response build':40s} {resp_ms:10.2f} ms", flush=True)
    print("-" * 60, flush=True)
    print(f"  {'TOTAL API response':40s} {total_ms:10.2f} ms  ({total_ms / 1000.0:.4f} s)", flush=True)
    print("=" * 60, flush=True)
    return ManualAnprAnalyzeResponse.model_validate(payload)


@router.post("/confirm", response_model=EventOut)
async def confirm_manual_capture(
    body: ManualAnprConfirmRequest,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.MANUAL_ANPR)),
) -> EventOut:
    """Create ENTRY/EXIT event after operator confirmation. Consumes pending capture."""
    pending, meta, snapshot_bytes, plate_crop_bytes = svc.load_pending(
        body.capture_id,
        operator_user_id=ctx.user.id,
    )
    analysis = meta.get("analysis") or {}
    if body.direction not in {Direction.ENTRY, Direction.EXIT}:
        raise ValidationAppError("Direction must be ENTRY or EXIT")

    ocr_conf = (
        body.ocr_confidence
        if body.ocr_confidence is not None
        else float(analysis.get("ocr_confidence") or 0.0)
    )
    plate_conf = (
        body.plate_confidence
        if body.plate_confidence is not None
        else float(analysis.get("plate_confidence") or 0.0)
    )
    combined = (
        body.combined_confidence
        if body.combined_confidence is not None
        else float(analysis.get("combined_confidence") or ocr_conf)
    )
    processing_ms = int(analysis.get("processing_ms") or 0)

    try:
        if is_firestore():
            camera = await camera_repo().get(pending.camera_id)
            if not camera:
                raise NotFoundError("Camera not found")
            ctx.ensure_org(camera.organization_id)
            ctx.ensure_site(camera.site_id)
            if camera.organization_id != pending.organization_id or camera.site_id != pending.site_id:
                raise ValidationAppError("Capture camera/site mismatch")
            site = await site_repo().get(camera.site_id)
            if not site:
                raise NotFoundError("Site not found")
            event, reason = await fs.ingest_event_fs(
                camera=camera,
                site=site,
                plate_text=body.plate_text.strip(),
                raw_ocr_text=str(analysis.get("raw_ocr") or body.plate_text),
                direction=body.direction,
                ocr_confidence=ocr_conf,
                plate_detection_confidence=plate_conf,
                vehicle_detection_confidence=combined,
                processing_duration_ms=processing_ms,
                source_type=SourceType.MANUAL,
                snapshot_bytes=snapshot_bytes,
                plate_crop_bytes=plate_crop_bytes,
                force=True,
                operator_user_id=ctx.user.id,
            )
            await write_audit(
                db,
                action=AuditAction.MANUAL_ANPR,
                user_id=ctx.user.id,
                organization_id=camera.organization_id,
                ip=client_ip(request),
                target_type="anpr_event",
                target_id=event.id if event else None,
                extra={
                    "source": "MANUAL",
                    "direction": str(body.direction),
                    "capture_id": body.capture_id,
                    "reason": reason,
                },
            )
            if event is None:
                raise NotFoundError("Event was not created")
            return EventOut.model_validate(fs.serialize_event_record(event, camera, site))

        assert db is not None
        camera = await _load_camera_sql(db, pending.camera_id)
        ctx.ensure_org(camera.organization_id)
        ctx.ensure_site(camera.site_id)
        site = await db.get(Site, camera.site_id)
        if not site:
            raise NotFoundError("Site not found")
        event, reason = await ingest_event(
            db,
            camera=camera,
            site=site,
            plate_text=body.plate_text.strip(),
            raw_ocr_text=str(analysis.get("raw_ocr") or body.plate_text),
            direction=body.direction,
            ocr_confidence=ocr_conf,
            plate_detection_confidence=plate_conf,
            vehicle_detection_confidence=combined,
            processing_duration_ms=processing_ms,
            source_type=SourceType.MANUAL,
            snapshot_bytes=snapshot_bytes,
            plate_crop_bytes=plate_crop_bytes,
            force=True,
            operator_user_id=ctx.user.id,
        )
        await write_audit(
            db,
            action=AuditAction.MANUAL_ANPR,
            user_id=ctx.user.id,
            organization_id=camera.organization_id,
            ip=client_ip(request),
            target_type="anpr_event",
            target_id=event.id if event else None,
            extra={
                "source": "MANUAL",
                "direction": str(body.direction),
                "capture_id": body.capture_id,
                "reason": reason,
            },
        )
        await db.commit()
        if event is None:
            raise NotFoundError("Event was not created")
        await db.refresh(event)
        return EventOut.model_validate(serialize_event(event, camera, site))
    finally:
        svc.delete_pending(body.capture_id)


@router.post("/cancel")
async def cancel_manual_capture(
    capture_id: str = Form(...),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.MANUAL_ANPR)),
) -> dict[str, str]:
    """Discard a pending capture without creating an event."""
    try:
        svc.load_pending(capture_id, operator_user_id=ctx.user.id)
    except NotFoundError:
        return {"status": "ok", "message": "already gone"}
    svc.delete_pending(capture_id)
    return {"status": "ok", "message": "cancelled"}
