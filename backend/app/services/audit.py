from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.enums import AuditAction

logger = get_logger(__name__)


async def write_audit(
    db: AsyncSession | None,
    *,
    action: AuditAction | str,
    user_id: str | None,
    organization_id: str | None,
    ip: str | None,
    target_type: str | None = None,
    target_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Persist audit row when SQLAlchemy session is present; otherwise structured log only."""
    if db is None:
        logger.info(
            "audit",
            action=str(action),
            user_id=user_id,
            organization_id=organization_id,
            ip=ip,
            target_type=target_type,
            target_id=target_id,
            extra=extra or {},
        )
        return
    from app.models.audit_log import AuditLog

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
