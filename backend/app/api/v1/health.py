from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_tenant, require_permission
from app.core.config import get_settings
from app.core.rbac import Permission
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
    db: AsyncSession = Depends(get_db),
    _ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.PLATFORM_ADMIN)),
) -> SystemHealth:
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_status = "error"
    storage_status = "ok"
    try:
        get_storage()
    except Exception:
        storage_status = "error"
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
    )
