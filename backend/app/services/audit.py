from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.enums import AuditAction


async def write_audit(
    db: AsyncSession,
    *,
    action: AuditAction | str,
    user_id: str | None,
    organization_id: str | None,
    ip: str | None,
    target_type: str | None = None,
    target_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            organization_id=organization_id,
            action=str(action),
            ip=ip,
            target_type=target_type,
            target_id=target_id,
            extra=extra or {},
        )
    )
