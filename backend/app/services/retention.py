from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.anpr_event import AnprEvent
from app.models.organization import Organization
from app.models.snapshot import Snapshot
from app.services.storage import get_storage

logger = get_logger(__name__)


async def apply_retention(db: AsyncSession) -> dict[str, int]:
    """Delete expired events/snapshots per organization retention_days policy."""
    orgs = (await db.execute(select(Organization))).scalars().all()
    deleted_events = 0
    deleted_snaps = 0
    storage = get_storage()
    now = datetime.now(UTC)
    for org in orgs:
        cutoff = now - timedelta(days=org.retention_days)
        snaps = (
            await db.execute(
                select(Snapshot).where(
                    Snapshot.organization_id == org.id,
                    Snapshot.created_at < cutoff,
                )
            )
        ).scalars().all()
        for snap in snaps:
            storage.delete(snap.storage_key)
            await db.delete(snap)
            deleted_snaps += 1
        result = await db.execute(
            delete(AnprEvent).where(AnprEvent.organization_id == org.id, AnprEvent.timestamp < cutoff)
        )
        deleted_events += result.rowcount or 0
        logger.info("retention.applied", org=org.slug, events=result.rowcount, snapshots=len(snaps))
    await db.commit()
    return {"events": deleted_events, "snapshots": deleted_snaps}
