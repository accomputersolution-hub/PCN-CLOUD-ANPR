from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_tenant, require_permission
from app.core.config import get_settings
from app.core.providers import (
    normalize_auth_provider,
    normalize_datastore_provider,
    normalize_storage_provider,
)
from app.core.rbac import Permission
from app.core.runtime import is_firestore
from app.models.camera import Camera
from app.models.edge_agent import EdgeAgent
from app.models.enums import CameraStatus, EdgeAgentStatus
from app.schemas.common import HealthResponse
from app.schemas.dashboard import SystemHealth
from app.services.storage import get_storage
from app.services.tenant import TenantContext

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service=get_settings().app_name, time=datetime.now(UTC))


@router.get("/health/system", response_model=SystemHealth)
async def system_health(
    db: AsyncSession | None = Depends(get_db),
    _ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.PLATFORM_ADMIN)),
) -> SystemHealth:
    settings = get_settings()
    auth_p = normalize_auth_provider(settings.auth_provider)
    ds_p = normalize_datastore_provider(settings.datastore_provider)
    storage_p = normalize_storage_provider(settings.storage_provider)

    storage_status = "ok"
    try:
        get_storage()
    except Exception:
        storage_status = "error"

    if is_firestore():
        offline = 0
        connected = 0
        try:
            from app.repositories import camera_repo, edge_agent_repo

            org_id = _ctx.organization_id
            if org_id:
                cams = await camera_repo().list_for_tenant(
                    organization_id=org_id, site_ids=_ctx.site_ids or None
                )
                offline = sum(1 for c in cams if c.status != "ONLINE")
                agents = await edge_agent_repo().list_for_tenant(
                    organization_id=org_id, site_ids=_ctx.site_ids or None
                )
                connected = sum(1 for a in agents if a.status == "CONNECTED")
        except Exception:
            pass
        return SystemHealth(
            api="ok",
            database="firestore",
            storage=storage_status,
            time=datetime.now(UTC),
            camera_offline_count=offline,
            edge_connected_count=connected,
            auth_provider=auth_p,
            datastore_provider=ds_p,
            storage_provider=storage_p,
        )

    assert db is not None
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"
    offline = int(
        (await db.execute(select(func.count(Camera.id)).where(Camera.status != CameraStatus.ONLINE))).scalar_one()
    )
    connected = int(
        (
            await db.execute(
                select(func.count(EdgeAgent.id)).where(EdgeAgent.status == EdgeAgentStatus.CONNECTED)
            )
        ).scalar_one()
    )
    return SystemHealth(
        api="ok",
        database=db_status,
        storage=storage_status,
        time=datetime.now(UTC),
        camera_offline_count=offline,
        edge_connected_count=connected,
        auth_provider=auth_p,
        datastore_provider=ds_p,
        storage_provider=storage_p,
    )
