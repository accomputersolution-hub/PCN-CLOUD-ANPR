from __future__ import annotations

import base64
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import client_ip, get_db, get_edge_agent, get_tenant, require_permission
from app.core.exceptions import NotFoundError, ValidationAppError
from app.core.rbac import Permission
from app.core.runtime import is_firestore
from app.core.security import hash_password
from app.domain.records import EdgeAgentRecord
from app.models.camera import Camera
from app.models.edge_agent import EdgeAgent
from app.models.enums import AuditAction, CameraStatus, EdgeAgentStatus, SourceType
from app.models.site import Site
from app.repositories import camera_repo, edge_agent_repo, site_repo
from app.schemas.camera import CameraOut, EdgeCameraConfigOut
from app.schemas.edge import (
    EdgeHeartbeatRequest,
    EdgeRegisterRequest,
    EdgeRegisterResponse,
    EdgeSyncRequest,
    EdgeSyncResponse,
)
from app.services import firestore_domain as fs
from app.services.audit import write_audit
from app.services.camera import camera_to_out, resolve_camera_rtsp_url, rtsp_configured
from app.services.event import ingest_event
from app.services.realtime import hub
from app.services.tenant import TenantContext

router = APIRouter(prefix="/edge", tags=["edge"])


async def _camera_out(db: AsyncSession, camera_id: str) -> CameraOut:
    stmt = select(Camera).options(selectinload(Camera.site), selectinload(Camera.gate)).where(Camera.id == camera_id)
    camera = (await db.execute(stmt)).scalar_one()
    return CameraOut.model_validate(camera_to_out(camera))


@router.post("/register", response_model=EdgeRegisterResponse)
async def register_edge(
    body: EdgeRegisterRequest,
    db: AsyncSession | None = Depends(get_db),
) -> EdgeRegisterResponse:
    if is_firestore():
        agent = await fs.register_edge_agent_fs(
            site_id=body.site_id, name=body.name, agent_key=body.agent_key
        )
        await hub.publish(agent.organization_id, "edge.status", {"id": agent.id, "status": agent.status})
        return EdgeRegisterResponse(
            agent_id=agent.id,
            site_id=agent.site_id,
            organization_id=agent.organization_id,
            message="Edge agent registered. Store the agent key securely; it cannot be recovered.",
        )

    assert db is not None
    site = await db.get(Site, body.site_id)
    if not site:
        raise NotFoundError("Site not found")
    agent = EdgeAgent(
        organization_id=site.organization_id,
        site_id=site.id,
        name=body.name,
        agent_key_hash=hash_password(body.agent_key),
        status=EdgeAgentStatus.CONNECTED,
        last_seen=datetime.now(UTC),
    )
    db.add(agent)
    await db.commit()
    await db.refresh(agent)
    await hub.publish(site.organization_id, "edge.status", {"id": agent.id, "status": agent.status})
    return EdgeRegisterResponse(
        agent_id=agent.id,
        site_id=site.id,
        organization_id=site.organization_id,
        message="Edge agent registered. Store the agent key securely; it cannot be recovered.",
    )


@router.get("/cameras", response_model=list[EdgeCameraConfigOut])
async def list_edge_cameras(
    db: AsyncSession | None = Depends(get_db),
    agent: EdgeAgent | EdgeAgentRecord = Depends(get_edge_agent),
) -> list[EdgeCameraConfigOut]:
    """Edge-only: returns RTSP URLs for cameras at this site. Never expose to PWA clients."""
    if is_firestore():
        rows = await camera_repo().list_for_tenant(
            organization_id=agent.organization_id,
            site_ids=None,
            site_id=agent.site_id,
        )
        out: list[EdgeCameraConfigOut] = []
        for cam in rows:
            if not cam.enabled:
                continue
            url = resolve_camera_rtsp_url(cam)  # type: ignore[arg-type]
            if not url and not cam.streaming:
                continue
            out.append(
                EdgeCameraConfigOut(
                    id=cam.id,
                    name=cam.name,
                    camera_code=cam.camera_code,
                    direction=cam.direction,
                    enabled=cam.enabled,
                    streaming=bool(cam.streaming),
                    frame_interval=0.5,
                    rtsp_url=url or "",
                )
            )
        return out

    assert db is not None
    stmt = select(Camera).where(Camera.site_id == agent.site_id, Camera.enabled.is_(True))
    rows = (await db.execute(stmt)).scalars().all()
    out = []
    for cam in rows:
        url = resolve_camera_rtsp_url(cam)
        if not url and not cam.streaming:
            continue
        out.append(
            EdgeCameraConfigOut(
                id=cam.id,
                name=cam.name,
                camera_code=cam.camera_code,
                direction=cam.direction,
                enabled=cam.enabled,
                streaming=bool(cam.streaming),
                frame_interval=0.5,
                rtsp_url=url or "",
            )
        )
    return out


@router.post("/cameras/{camera_id}/start", response_model=CameraOut)
async def start_edge_camera(
    camera_id: str,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.CAMERA_WRITE)),
) -> CameraOut:
    if is_firestore():
        camera = await camera_repo().get(camera_id)
        if not camera:
            raise NotFoundError("Camera not found")
        ctx.ensure_org(camera.organization_id)
        ctx.ensure_site(camera.site_id)
        if not rtsp_configured(camera):  # type: ignore[arg-type]
            raise ValidationAppError("Camera has no RTSP URL configured")
        if not camera.enabled:
            raise ValidationAppError("Enable the camera before starting RTSP capture")
        camera.streaming = True
        camera.status = "CONNECTING"
        camera.connection_error = None
        await camera_repo().save(camera)
        await write_audit(
            db,
            action=AuditAction.CAMERA_UPDATE,
            user_id=ctx.user.id,
            organization_id=camera.organization_id,
            ip=client_ip(request),
            target_type="camera",
            target_id=camera.id,
            extra={"streaming": True},
        )
        await hub.publish(
            camera.organization_id,
            "camera.status",
            {"id": camera.id, "status": camera.status, "streaming": True},
        )
        return CameraOut.model_validate(await fs.camera_out_enriched(camera))

    assert db is not None
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise NotFoundError("Camera not found")
    ctx.ensure_org(camera.organization_id)
    ctx.ensure_site(camera.site_id)
    if not rtsp_configured(camera):
        raise ValidationAppError("Camera has no RTSP URL configured")
    if not camera.enabled:
        raise ValidationAppError("Enable the camera before starting RTSP capture")
    camera.streaming = True
    camera.status = CameraStatus.CONNECTING
    camera.connection_error = None
    await write_audit(
        db,
        action=AuditAction.CAMERA_UPDATE,
        user_id=ctx.user.id,
        organization_id=camera.organization_id,
        ip=client_ip(request),
        target_type="camera",
        target_id=camera.id,
        extra={"streaming": True},
    )
    await db.commit()
    await hub.publish(
        camera.organization_id,
        "camera.status",
        {"id": camera.id, "status": camera.status, "streaming": True},
    )
    return await _camera_out(db, camera.id)


@router.post("/cameras/{camera_id}/stop", response_model=CameraOut)
async def stop_edge_camera(
    camera_id: str,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.CAMERA_WRITE)),
) -> CameraOut:
    if is_firestore():
        camera = await camera_repo().get(camera_id)
        if not camera:
            raise NotFoundError("Camera not found")
        ctx.ensure_org(camera.organization_id)
        ctx.ensure_site(camera.site_id)
        camera.streaming = False
        if camera.status == "CONNECTING":
            camera.status = "OFFLINE"
        await camera_repo().save(camera)
        await write_audit(
            db,
            action=AuditAction.CAMERA_UPDATE,
            user_id=ctx.user.id,
            organization_id=camera.organization_id,
            ip=client_ip(request),
            target_type="camera",
            target_id=camera.id,
            extra={"streaming": False},
        )
        await hub.publish(
            camera.organization_id,
            "camera.status",
            {"id": camera.id, "status": camera.status, "streaming": False},
        )
        return CameraOut.model_validate(await fs.camera_out_enriched(camera))

    assert db is not None
    camera = await db.get(Camera, camera_id)
    if not camera:
        raise NotFoundError("Camera not found")
    ctx.ensure_org(camera.organization_id)
    ctx.ensure_site(camera.site_id)
    camera.streaming = False
    if camera.status == CameraStatus.CONNECTING:
        camera.status = CameraStatus.OFFLINE
    await write_audit(
        db,
        action=AuditAction.CAMERA_UPDATE,
        user_id=ctx.user.id,
        organization_id=camera.organization_id,
        ip=client_ip(request),
        target_type="camera",
        target_id=camera.id,
        extra={"streaming": False},
    )
    await db.commit()
    await hub.publish(
        camera.organization_id,
        "camera.status",
        {"id": camera.id, "status": camera.status, "streaming": False},
    )
    return await _camera_out(db, camera.id)


@router.post("/heartbeat")
async def heartbeat(
    body: EdgeHeartbeatRequest,
    db: AsyncSession | None = Depends(get_db),
    agent: EdgeAgent | EdgeAgentRecord = Depends(get_edge_agent),
) -> dict:
    if is_firestore():
        assert isinstance(agent, EdgeAgentRecord)
        await fs.heartbeat_edge_agent_fs(
            agent,
            cpu_usage=body.cpu_usage,
            memory_usage=body.memory_usage,
            queue_size=body.queue_size,
            camera_statuses=list(body.camera_statuses or []),
        )
        return {"ok": True}

    assert db is not None
    assert isinstance(agent, EdgeAgent)
    agent.status = EdgeAgentStatus.CONNECTED
    agent.last_seen = datetime.now(UTC)
    agent.cpu_usage = body.cpu_usage
    agent.memory_usage = body.memory_usage
    agent.queue_size = body.queue_size
    for item in body.camera_statuses:
        camera = await db.get(Camera, item.get("id"))
        if camera is None or camera.site_id != agent.site_id:
            continue
        if "status" in item:
            try:
                camera.status = CameraStatus(item["status"])
            except ValueError:
                pass
        if "fps" in item:
            camera.fps = item["fps"]
        if "retry_count" in item:
            camera.retry_count = item["retry_count"]
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
    await db.commit()
    await hub.publish(agent.organization_id, "edge.status", {"id": agent.id, "status": agent.status})
    return {"ok": True}


@router.post("/events", response_model=EdgeSyncResponse)
@router.post("/sync", response_model=EdgeSyncResponse)
async def sync_events(
    body: EdgeSyncRequest,
    db: AsyncSession | None = Depends(get_db),
    agent: EdgeAgent | EdgeAgentRecord = Depends(get_edge_agent),
) -> EdgeSyncResponse:
    accepted = duplicates = rejected = 0
    ids: list[str] = []

    if is_firestore():
        site = await site_repo().get(agent.site_id)
        if not site:
            raise NotFoundError("Site not found")
        for item in body.events:
            camera = await camera_repo().get(item.camera_id)
            if camera is None or camera.site_id != agent.site_id:
                rejected += 1
                continue
            snapshot = base64.b64decode(item.snapshot_b64) if item.snapshot_b64 else None
            plate = base64.b64decode(item.plate_crop_b64) if item.plate_crop_b64 else None
            vehicle = base64.b64decode(item.vehicle_crop_b64) if item.vehicle_crop_b64 else None
            event, reason = await fs.ingest_event_fs(
                camera=camera,
                site=site,
                plate_text=item.plate_text,
                raw_ocr_text=item.raw_ocr_text,
                direction=item.direction,
                ocr_confidence=item.ocr_confidence,
                plate_detection_confidence=item.plate_detection_confidence,
                vehicle_detection_confidence=item.vehicle_detection_confidence,
                timestamp=item.timestamp,
                local_timestamp=item.local_timestamp,
                processing_duration_ms=item.processing_duration_ms,
                source_type=item.source_type or SourceType.EDGE,
                event_id=item.id,
                snapshot_bytes=snapshot,
                plate_crop_bytes=plate,
                vehicle_crop_bytes=vehicle,
            )
            if reason == "created":
                accepted += 1
            elif reason in {"duplicate", "idempotent"}:
                duplicates += 1
            else:
                rejected += 1
            if event:
                ids.append(event.id)
        assert isinstance(agent, EdgeAgentRecord)
        agent.last_sync_at = datetime.now(UTC)
        agent.queue_size = 0
        await edge_agent_repo().save(agent)
        await hub.publish(agent.organization_id, "sync.status", {"agent_id": agent.id, "accepted": accepted})
        return EdgeSyncResponse(accepted=accepted, duplicates=duplicates, rejected=rejected, event_ids=ids)

    assert db is not None
    site = await db.get(Site, agent.site_id)
    if not site:
        raise NotFoundError("Site not found")
    for item in body.events:
        camera = await db.get(Camera, item.camera_id)
        if camera is None or camera.site_id != agent.site_id:
            rejected += 1
            continue
        snapshot = base64.b64decode(item.snapshot_b64) if item.snapshot_b64 else None
        plate = base64.b64decode(item.plate_crop_b64) if item.plate_crop_b64 else None
        vehicle = base64.b64decode(item.vehicle_crop_b64) if item.vehicle_crop_b64 else None
        event, reason = await ingest_event(
            db,
            camera=camera,
            site=site,
            plate_text=item.plate_text,
            raw_ocr_text=item.raw_ocr_text,
            direction=item.direction,
            ocr_confidence=item.ocr_confidence,
            plate_detection_confidence=item.plate_detection_confidence,
            vehicle_detection_confidence=item.vehicle_detection_confidence,
            timestamp=item.timestamp,
            local_timestamp=item.local_timestamp,
            processing_duration_ms=item.processing_duration_ms,
            source_type=item.source_type or SourceType.EDGE,
            event_id=item.id,
            snapshot_bytes=snapshot,
            plate_crop_bytes=plate,
            vehicle_crop_bytes=vehicle,
        )
        if reason == "created":
            accepted += 1
        elif reason in {"duplicate", "idempotent"}:
            duplicates += 1
        else:
            rejected += 1
        if event:
            ids.append(event.id)
    agent.last_sync_at = datetime.now(UTC)
    agent.queue_size = 0
    await db.commit()
    await hub.publish(agent.organization_id, "sync.status", {"agent_id": agent.id, "accepted": accepted})
    return EdgeSyncResponse(accepted=accepted, duplicates=duplicates, rejected=rejected, event_ids=ids)
