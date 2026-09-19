from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import client_ip, get_db, get_tenant, require_permission
from app.core.exceptions import NotFoundError, ValidationAppError
from app.core.rbac import Permission
from app.core.runtime import is_firestore
from app.models.camera import Camera
from app.models.enums import AuditAction, CameraStatus
from app.models.gate import Gate
from app.models.gateway import Gateway
from app.models.nvr import Nvr
from app.models.site import Site
from app.schemas.camera import (
    CameraCreate,
    CameraHealthOut,
    CameraOut,
    CameraStatusOut,
    CameraTestRequest,
    CameraTestResult,
    CameraUpdate,
    EnableBody,
)
from app.services import firestore_domain as fs
from app.services.audit import write_audit
from app.services.camera import (
    camera_to_out,
    encrypt_optional,
    test_camera_connection,
    test_rtsp_for_camera,
)
from app.services.realtime import hub
from app.services.tenant import TenantContext

router = APIRouter(prefix="/cameras", tags=["cameras"])


async def _get_camera(db: AsyncSession, camera_id: str, ctx: TenantContext) -> Camera:
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


async def _bind_nvr_and_gateway(db: AsyncSession, site_id: str, nvr_id: str | None, gateway_id: str | None) -> None:
    if nvr_id:
        nvr = await db.get(Nvr, nvr_id)
        if nvr is None or nvr.site_id != site_id:
            raise ValidationAppError("NVR does not belong to the selected site")
    if gateway_id:
        gw = await db.get(Gateway, gateway_id)
        if gw is None or gw.site_id != site_id:
            raise ValidationAppError("Gateway does not belong to the selected site")


@router.get("", response_model=list[CameraOut])
async def list_cameras(
    site_id: str | None = None,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.CAMERA_READ)),
) -> list[CameraOut]:
    if is_firestore():
        rows = await fs.list_cameras(ctx, site_id=site_id)
        return [CameraOut.model_validate(r) for r in rows]
    assert db is not None
    stmt = select(Camera).options(selectinload(Camera.site), selectinload(Camera.gate)).order_by(Camera.name)
    stmt = ctx.apply_org(stmt, Camera.organization_id)
    stmt = ctx.apply_site(stmt, Camera.site_id)
    if site_id:
        ctx.ensure_site(site_id)
        stmt = stmt.where(Camera.site_id == site_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [CameraOut.model_validate(camera_to_out(c)) for c in rows]


@router.get("/status", response_model=list[CameraStatusOut])
async def camera_status(
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.CAMERA_READ)),
) -> list[CameraStatusOut]:
    if is_firestore():
        rows = await fs.list_cameras(ctx)
        return [CameraStatusOut.model_validate(r) for r in rows]
    assert db is not None
    stmt = select(Camera)
    stmt = ctx.apply_org(stmt, Camera.organization_id)
    stmt = ctx.apply_site(stmt, Camera.site_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [CameraStatusOut.model_validate(c) for c in rows]


@router.get("/{camera_id}", response_model=CameraOut)
async def get_camera(
    camera_id: str,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.CAMERA_READ)),
) -> CameraOut:
    if is_firestore():
        return CameraOut.model_validate(await fs.get_camera_out(ctx, camera_id))
    assert db is not None
    camera = await _get_camera(db, camera_id, ctx)
    return CameraOut.model_validate(camera_to_out(camera))


@router.get("/{camera_id}/health", response_model=CameraHealthOut)
async def get_camera_health(
    camera_id: str,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.CAMERA_READ)),
) -> CameraHealthOut:
    if is_firestore():
        out = await fs.get_camera_out(ctx, camera_id)
    else:
        assert db is not None
        camera = await _get_camera(db, camera_id, ctx)
        out = camera_to_out(camera)
    return CameraHealthOut.model_validate(
        {
            "id": out["id"],
            "name": out["name"],
            "status": out["status"],
            "enabled": out["enabled"],
            "streaming": out["streaming"],
            "rtsp_configured": out["rtsp_configured"],
            "last_heartbeat": out["last_heartbeat"],
            "last_frame_at": out["last_frame_at"],
            "fps": out["fps"],
            "retry_count": out["retry_count"],
            "connection_error": out["connection_error"],
            "resolution": out["resolution"],
        }
    )


@router.post("", response_model=CameraOut, status_code=201)
async def create_camera(
    body: CameraCreate,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.CAMERA_WRITE)),
) -> CameraOut:
    if is_firestore():
        out = await fs.create_camera(ctx, body)
        await write_audit(
            db,
            action=AuditAction.CAMERA_CREATE,
            user_id=ctx.user.id,
            organization_id=out["organization_id"],
            ip=client_ip(request),
            target_type="camera",
            target_id=out["id"],
            extra={"name": out["name"]},
        )
        return CameraOut.model_validate(out)

    assert db is not None
    site = await db.get(Site, body.site_id)
    gate = await db.get(Gate, body.gate_id)
    if not site or not gate:
        raise NotFoundError("Site or gate not found")
    if gate.site_id != site.id:
        raise ValidationAppError("Gate does not belong to the selected site")
    ctx.ensure_org(site.organization_id)
    ctx.ensure_site(site.id)
    await _bind_nvr_and_gateway(db, site.id, body.nvr_id, body.gateway_id)
    camera = Camera(
        organization_id=site.organization_id,
        site_id=site.id,
        gate_id=gate.id,
        name=body.name,
        camera_code=body.camera_code,
        direction=body.direction,
        rtsp_url_encrypted=encrypt_optional(body.rtsp_url),
        onvif_ip=body.onvif_ip,
        username_encrypted=encrypt_optional(body.username),
        password_encrypted=encrypt_optional(body.password),
        stream_type=body.stream_type,
        resolution=body.resolution,
        enabled=body.enabled,
        status=CameraStatus.UNKNOWN,
        source_type=body.source_type,
        nvr_id=body.nvr_id,
        channel=body.channel,
        anpr_enabled=body.anpr_enabled,
        gateway_id=body.gateway_id,
        anpr_roi=(body.anpr_roi.model_dump() if body.anpr_roi is not None else None),
    )
    db.add(camera)
    await db.flush()
    await write_audit(
        db,
        action=AuditAction.CAMERA_CREATE,
        user_id=ctx.user.id,
        organization_id=site.organization_id,
        ip=client_ip(request),
        target_type="camera",
        target_id=camera.id,
        extra={"name": camera.name},
    )
    await db.commit()
    camera = await _get_camera(db, camera.id, ctx)
    return CameraOut.model_validate(camera_to_out(camera))


@router.patch("/{camera_id}", response_model=CameraOut)
async def update_camera(
    camera_id: str,
    body: CameraUpdate,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.CAMERA_WRITE)),
) -> CameraOut:
    if is_firestore():
        out = await fs.update_camera(ctx, camera_id, body)
        await write_audit(
            db,
            action=AuditAction.CAMERA_UPDATE,
            user_id=ctx.user.id,
            organization_id=out["organization_id"],
            ip=client_ip(request),
            target_type="camera",
            target_id=out["id"],
        )
        return CameraOut.model_validate(out)

    assert db is not None
    camera = await _get_camera(db, camera_id, ctx)
    data = body.model_dump(exclude_unset=True)
    if "rtsp_url" in data:
        camera.rtsp_url_encrypted = encrypt_optional(data.pop("rtsp_url"))
    if "username" in data:
        camera.username_encrypted = encrypt_optional(data.pop("username"))
    if "password" in data:
        camera.password_encrypted = encrypt_optional(data.pop("password"))
    nvr_id = data.get("nvr_id", camera.nvr_id)
    gateway_id = data.get("gateway_id", camera.gateway_id)
    await _bind_nvr_and_gateway(db, camera.site_id, nvr_id, gateway_id)
    for key, value in data.items():
        setattr(camera, key, value)
    await write_audit(
        db,
        action=AuditAction.CAMERA_UPDATE,
        user_id=ctx.user.id,
        organization_id=camera.organization_id,
        ip=client_ip(request),
        target_type="camera",
        target_id=camera.id,
    )
    await db.commit()
    camera = await _get_camera(db, camera.id, ctx)
    return CameraOut.model_validate(camera_to_out(camera))


@router.delete("/{camera_id}", status_code=204, response_class=Response)
async def delete_camera(
    camera_id: str,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.CAMERA_WRITE)),
) -> Response:
    if is_firestore():
        camera = await fs.get_camera(ctx, camera_id)
        await write_audit(
            db,
            action=AuditAction.CAMERA_DELETE,
            user_id=ctx.user.id,
            organization_id=camera.organization_id,
            ip=client_ip(request),
            target_type="camera",
            target_id=camera.id,
        )
        await fs.delete_camera(ctx, camera_id)
        return Response(status_code=204)

    assert db is not None
    camera = await _get_camera(db, camera_id, ctx)
    await write_audit(
        db,
        action=AuditAction.CAMERA_DELETE,
        user_id=ctx.user.id,
        organization_id=camera.organization_id,
        ip=client_ip(request),
        target_type="camera",
        target_id=camera.id,
    )
    await db.delete(camera)
    await db.commit()
    return Response(status_code=204)


@router.post("/{camera_id}/enable", response_model=CameraOut)
async def enable_camera(
    camera_id: str,
    body: EnableBody,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.CAMERA_WRITE)),
) -> CameraOut:
    if is_firestore():
        out = await fs.enable_camera(ctx, camera_id, body.enabled)
        await write_audit(
            db,
            action=AuditAction.CAMERA_ENABLE if body.enabled else AuditAction.CAMERA_DISABLE,
            user_id=ctx.user.id,
            organization_id=out["organization_id"],
            ip=client_ip(request),
            target_type="camera",
            target_id=out["id"],
        )
        return CameraOut.model_validate(out)

    assert db is not None
    camera = await _get_camera(db, camera_id, ctx)
    camera.enabled = body.enabled
    await write_audit(
        db,
        action=AuditAction.CAMERA_ENABLE if body.enabled else AuditAction.CAMERA_DISABLE,
        user_id=ctx.user.id,
        organization_id=camera.organization_id,
        ip=client_ip(request),
        target_type="camera",
        target_id=camera.id,
    )
    await db.commit()
    await hub.publish(camera.organization_id, "camera.status", {"id": camera.id, "enabled": camera.enabled})
    camera = await _get_camera(db, camera.id, ctx)
    return CameraOut.model_validate(camera_to_out(camera))


@router.post("/test", response_model=CameraTestResult)
async def test_camera(
    body: CameraTestRequest,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.CAMERA_TEST)),
) -> CameraTestResult:
    """Legacy test endpoint. Prefer POST /cameras/{id}/test-rtsp for stored cameras."""
    if body.camera_id:
        if is_firestore():
            from app.repositories import camera_repo

            camera = await fs.get_camera(ctx, body.camera_id)
            await write_audit(
                db,
                action=AuditAction.CAMERA_TEST,
                user_id=ctx.user.id,
                organization_id=camera.organization_id,
                ip=client_ip(request),
                target_type="camera",
                target_id=camera.id,
            )
            result = test_rtsp_for_camera(camera)  # type: ignore[arg-type]
            if hasattr(camera.status, "value"):
                camera.status = str(camera.status.value)
            else:
                camera.status = str(camera.status)
            await camera_repo().save(camera)
            return CameraTestResult.model_validate(result)

        assert db is not None
        camera = await _get_camera(db, body.camera_id, ctx)
        await write_audit(
            db,
            action=AuditAction.CAMERA_TEST,
            user_id=ctx.user.id,
            organization_id=camera.organization_id,
            ip=client_ip(request),
            target_type="camera",
            target_id=camera.id,
        )
        result = test_rtsp_for_camera(camera)
        await db.commit()
        return CameraTestResult.model_validate(result)

    result = test_camera_connection(body.rtsp_url)
    return CameraTestResult.model_validate(result)


@router.post("/{camera_id}/test-rtsp", response_model=CameraTestResult)
async def test_camera_rtsp(
    camera_id: str,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.CAMERA_TEST)),
) -> CameraTestResult:
    if is_firestore():
        from app.repositories import camera_repo

        camera = await fs.get_camera(ctx, camera_id)
        await write_audit(
            db,
            action=AuditAction.CAMERA_TEST,
            user_id=ctx.user.id,
            organization_id=camera.organization_id,
            ip=client_ip(request),
            target_type="camera",
            target_id=camera.id,
        )
        result = test_rtsp_for_camera(camera)  # type: ignore[arg-type]
        if hasattr(camera.status, "value"):
            camera.status = str(camera.status.value)
        else:
            camera.status = str(camera.status)
        await camera_repo().save(camera)
        await hub.publish(
            camera.organization_id,
            "camera.status",
            {"id": camera.id, "status": camera.status, "connection_error": camera.connection_error},
        )
        return CameraTestResult.model_validate(result)

    assert db is not None
    camera = await _get_camera(db, camera_id, ctx)
    await write_audit(
        db,
        action=AuditAction.CAMERA_TEST,
        user_id=ctx.user.id,
        organization_id=camera.organization_id,
        ip=client_ip(request),
        target_type="camera",
        target_id=camera.id,
    )
    result = test_rtsp_for_camera(camera)
    await db.commit()
    await hub.publish(
        camera.organization_id,
        "camera.status",
        {"id": camera.id, "status": camera.status, "connection_error": camera.connection_error},
    )
    return CameraTestResult.model_validate(result)
