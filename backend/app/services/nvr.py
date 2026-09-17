from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationAppError
from app.core.runtime import is_firestore
from app.domain.connectivity import NvrRecord
from app.models.camera import Camera
from app.models.site import Site
from app.repositories import camera_repo, gateway_repo, nvr_repo, site_repo
from app.schemas.nvr import NvrOut, NvrUpdate
from app.services.gateway import load_site_for_ctx
from app.services.tenant import TenantContext


async def _to_out(db: AsyncSession | None, record: NvrRecord) -> NvrOut:
    if is_firestore() or db is None:
        cameras = await camera_repo(db).list_for_tenant(
            organization_id=record.organization_id,
            site_ids=None,
            site_id=record.site_id,
        )
        cam_count = sum(1 for c in cameras if c.nvr_id == record.id)
        site = await site_repo(db).get(record.site_id)
        site_name = site.name if site else None
    else:
        cam_count = int(
            (await db.execute(select(func.count(Camera.id)).where(Camera.nvr_id == record.id))).scalar_one()
        )
        site = await db.get(Site, record.site_id)
        site_name = site.name if site else None
    return NvrOut.model_validate(
        {**record.model_dump(), "camera_count": cam_count, "site_name": site_name}
    )


async def create_nvr(
    db: AsyncSession | None,
    ctx: TenantContext,
    *,
    site_id: str,
    name: str,
    vendor: str,
    model: str,
    host: str,
    channel_count: int,
    gateway_id: str | None,
    enabled: bool,
    notes: str,
) -> NvrOut:
    site = await load_site_for_ctx(db, ctx, site_id)
    if gateway_id:
        gw = await gateway_repo(db).get(gateway_id)
        if gw is None or gw.site_id != site.id:
            raise ValidationAppError("Gateway does not belong to this site")
    record = NvrRecord(
        id=str(uuid4()),
        organization_id=site.organization_id,
        site_id=site.id,
        gateway_id=gateway_id,
        name=name,
        vendor=vendor or "GENERIC",
        model=model or "",
        host=host or "",
        channel_count=channel_count,
        enabled=enabled,
        notes=notes or "",
    )
    saved = await nvr_repo(db).add(record)
    return await _to_out(db, saved)


async def list_nvrs(db: AsyncSession | None, ctx: TenantContext, site_id: str | None = None) -> list[NvrOut]:
    if site_id:
        await load_site_for_ctx(db, ctx, site_id)
    org_id = None if ctx.is_super else ctx.organization_id
    records = await nvr_repo(db).list_for_tenant(
        organization_id=org_id,
        site_ids=ctx.site_ids or None,
        site_id=site_id,
    )
    return [await _to_out(db, r) for r in records]


async def get_nvr(db: AsyncSession | None, ctx: TenantContext, nvr_id: str) -> NvrOut:
    record = await nvr_repo(db).get(nvr_id)
    if record is None:
        raise NotFoundError("NVR not found")
    ctx.ensure_org(record.organization_id)
    ctx.ensure_site(record.site_id)
    return await _to_out(db, record)


async def update_nvr(db: AsyncSession | None, ctx: TenantContext, nvr_id: str, body: NvrUpdate) -> NvrOut:
    record = await nvr_repo(db).get(nvr_id)
    if record is None:
        raise NotFoundError("NVR not found")
    ctx.ensure_org(record.organization_id)
    ctx.ensure_site(record.site_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("gateway_id"):
        gw = await gateway_repo(db).get(data["gateway_id"])
        if gw is None or gw.site_id != record.site_id:
            raise ValidationAppError("Gateway does not belong to this site")
    for key, value in data.items():
        setattr(record, key, value)
    saved = await nvr_repo(db).save(record)
    return await _to_out(db, saved)


async def delete_nvr(db: AsyncSession | None, ctx: TenantContext, nvr_id: str) -> None:
    record = await nvr_repo(db).get(nvr_id)
    if record is None:
        raise NotFoundError("NVR not found")
    ctx.ensure_org(record.organization_id)
    ctx.ensure_site(record.site_id)
    await nvr_repo(db).delete(nvr_id)
