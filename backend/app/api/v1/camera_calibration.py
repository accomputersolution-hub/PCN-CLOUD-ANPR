"""ANPR camera calibration wizard API (upload test frame; no events)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_db, get_tenant
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationAppError
from app.core.rbac import Permission, has_permission
from app.core.runtime import is_firestore
from app.domain.records import AnprCalibrationRecord
from app.models.camera import Camera
from app.models.enums import UserRole
from app.schemas.camera import AnprCalibrationStoredOut, CameraCalibrateResponse
from app.services import camera_calibration as cal_svc
from app.services import firestore_domain as fs
from app.services.tenant import TenantContext

router = APIRouter(prefix="/cameras", tags=["camera-calibration"])


def _principal_role(user: Any) -> UserRole:
    raw = getattr(user, "role", None)
    if isinstance(raw, UserRole):
        return raw
    return UserRole(str(raw))


async def require_calibrate_permission(user: Any = Depends(get_current_user)) -> Any:
    """CAMERA_WRITE or MANUAL_ANPR (installer / guard on-site)."""
    role = _principal_role(user)
    if has_permission(role, Permission.CAMERA_WRITE) or has_permission(role, Permission.MANUAL_ANPR):
        return user
    raise ForbiddenError("Missing permission: camera:write or manual_anpr:write")


async def _get_sql_camera(db: AsyncSession, camera_id: str, ctx: TenantContext) -> Camera:
    stmt = (
        select(Camera)
        .options(selectinload(Camera.site), selectinload(Camera.gate))
        .where(Camera.id == camera_id)
    )
    camera = (await db.execute(stmt)).scalar_one_or_none()
    if not camera:
        raise NotFoundError("Camera not found")
    ctx.ensure_org(camera.organization_id)
    ctx.ensure_site(camera.site_id)
    return camera


def _suffix(filename: str | None) -> str:
    name = (filename or "").lower()
    for ext in (".png", ".webp", ".bmp", ".jpeg", ".jpg"):
        if name.endswith(ext):
            return ext if ext != ".jpeg" else ".jpg"
    return ".jpg"


@router.post("/{camera_id}/calibrate", response_model=CameraCalibrateResponse)
async def calibrate_camera(
    camera_id: str,
    file: UploadFile = File(...),
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_calibrate_permission),
) -> CameraCalibrateResponse:
    data = await file.read()
    if not data:
        raise ValidationAppError("Empty image upload")
    suffix = _suffix(file.filename)

    if is_firestore():
        camera = await fs.get_camera(ctx, camera_id)
        out = cal_svc.run_calibration_on_bytes(
            data, camera=camera, camera_id=camera_id, suffix=suffix
        )
        stored = out["anpr_calibration"]
        camera.anpr_calibration = AnprCalibrationRecord.model_validate(stored)
        from app.repositories import camera_repo

        await camera_repo().save(camera)
        report = out["report"]
        return CameraCalibrateResponse(
            camera_id=camera_id,
            status=str(report.get("status") or "RED"),
            overall_score=float(report.get("overall_score") or 0),
            component_scores=dict(report.get("component_scores") or {}),
            reasons=list(report.get("reasons") or []),
            guidance=list(report.get("guidance") or []),
            metrics=dict(report.get("metrics") or {}),
            targets=dict(report.get("targets") or {}),
            overlays=dict(report.get("overlays") or {}),
            anpr_calibration=AnprCalibrationStoredOut.model_validate(stored),
            previous=out.get("previous"),
        )

    assert db is not None
    camera = await _get_sql_camera(db, camera_id, ctx)
    out = cal_svc.run_calibration_on_bytes(
        data, camera=camera, camera_id=camera_id, suffix=suffix
    )
    stored = out["anpr_calibration"]
    camera.anpr_calibration = stored
    await db.commit()
    await db.refresh(camera)
    report = out["report"]
    return CameraCalibrateResponse(
        camera_id=camera_id,
        status=str(report.get("status") or "RED"),
        overall_score=float(report.get("overall_score") or 0),
        component_scores=dict(report.get("component_scores") or {}),
        reasons=list(report.get("reasons") or []),
        guidance=list(report.get("guidance") or []),
        metrics=dict(report.get("metrics") or {}),
        targets=dict(report.get("targets") or {}),
        overlays=dict(report.get("overlays") or {}),
        anpr_calibration=AnprCalibrationStoredOut.model_validate(stored),
        previous=out.get("previous"),
    )


@router.get("/{camera_id}/calibration", response_model=AnprCalibrationStoredOut | None)
async def get_camera_calibration(
    camera_id: str,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_calibrate_permission),
) -> AnprCalibrationStoredOut | None:
    if is_firestore():
        camera = await fs.get_camera(ctx, camera_id)
        raw = getattr(camera, "anpr_calibration", None)
        if raw is None:
            return None
        payload = raw.model_dump() if hasattr(raw, "model_dump") else raw
        return AnprCalibrationStoredOut.model_validate(payload)

    assert db is not None
    camera = await _get_sql_camera(db, camera_id, ctx)
    raw = getattr(camera, "anpr_calibration", None)
    if not raw:
        return None
    return AnprCalibrationStoredOut.model_validate(raw)
