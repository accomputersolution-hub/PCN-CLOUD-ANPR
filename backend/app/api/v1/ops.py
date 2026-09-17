from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_tenant, require_permission
from app.core.rbac import Permission
from app.models.audit_log import AuditLog
from app.services.retention import apply_retention
from app.services.tenant import TenantContext

router = APIRouter(tags=["ops"])


@router.get("/audit")
async def list_audit(
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.AUDIT_READ)),
) -> list[dict]:
    stmt = select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit)
    if not ctx.is_super and ctx.organization_id:
        stmt = stmt.where(AuditLog.organization_id == ctx.organization_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "organization_id": r.organization_id,
            "action": r.action,
            "timestamp": r.timestamp,
            "ip": r.ip,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "extra": r.extra,
        }
        for r in rows
    ]


@router.post("/retention/run")
async def run_retention(
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_permission(Permission.PLATFORM_ADMIN)),
) -> dict:
    return await apply_retention(db)
