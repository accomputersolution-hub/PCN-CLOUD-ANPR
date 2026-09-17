from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.connectivity import GatewayRecord, NvrRecord, SiteConnectivityRecord
from app.models.gateway import Gateway
from app.models.nvr import Nvr
from app.models.site import Site


def _gateway_to_record(row: Gateway) -> GatewayRecord:
    return GatewayRecord.model_validate(row, from_attributes=True)


def _nvr_to_record(row: Nvr) -> NvrRecord:
    return NvrRecord.model_validate(row, from_attributes=True)


class SqlAlchemyGatewayRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, gateway_id: str) -> GatewayRecord | None:
        row = await self.db.get(Gateway, gateway_id)
        return _gateway_to_record(row) if row else None

    async def list_for_tenant(
        self,
        *,
        organization_id: str | None,
        site_ids: list[str] | None,
        site_id: str | None = None,
    ) -> list[GatewayRecord]:
        stmt = select(Gateway).order_by(Gateway.name)
        if organization_id:
            stmt = stmt.where(Gateway.organization_id == organization_id)
        if site_ids:
            stmt = stmt.where(Gateway.site_id.in_(site_ids))
        if site_id:
            stmt = stmt.where(Gateway.site_id == site_id)
        rows = (await self.db.execute(stmt)).scalars().all()
        return [_gateway_to_record(r) for r in rows]

    async def add(self, record: GatewayRecord) -> GatewayRecord:
        row = Gateway(**record.model_dump())
        self.db.add(row)
        await self.db.flush()
        return _gateway_to_record(row)

    async def save(self, record: GatewayRecord) -> GatewayRecord:
        row = await self.db.get(Gateway, record.id)
        if row is None:
            return await self.add(record)
        for key, value in record.model_dump().items():
            setattr(row, key, value)
        await self.db.flush()
        return _gateway_to_record(row)

    async def delete(self, gateway_id: str) -> None:
        row = await self.db.get(Gateway, gateway_id)
        if row is not None:
            await self.db.delete(row)
            await self.db.flush()


class SqlAlchemyNvrRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, nvr_id: str) -> NvrRecord | None:
        row = await self.db.get(Nvr, nvr_id)
        return _nvr_to_record(row) if row else None

    async def list_for_tenant(
        self,
        *,
        organization_id: str | None,
        site_ids: list[str] | None,
        site_id: str | None = None,
    ) -> list[NvrRecord]:
        stmt = select(Nvr).order_by(Nvr.name)
        if organization_id:
            stmt = stmt.where(Nvr.organization_id == organization_id)
        if site_ids:
            stmt = stmt.where(Nvr.site_id.in_(site_ids))
        if site_id:
            stmt = stmt.where(Nvr.site_id == site_id)
        rows = (await self.db.execute(stmt)).scalars().all()
        return [_nvr_to_record(r) for r in rows]

    async def add(self, record: NvrRecord) -> NvrRecord:
        row = Nvr(**record.model_dump())
        self.db.add(row)
        await self.db.flush()
        return _nvr_to_record(row)

    async def save(self, record: NvrRecord) -> NvrRecord:
        row = await self.db.get(Nvr, record.id)
        if row is None:
            return await self.add(record)
        for key, value in record.model_dump().items():
            setattr(row, key, value)
        await self.db.flush()
        return _nvr_to_record(row)

    async def delete(self, nvr_id: str) -> None:
        row = await self.db.get(Nvr, nvr_id)
        if row is not None:
            await self.db.delete(row)
            await self.db.flush()


class SqlAlchemySiteConnectivityRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get(self, site_id: str) -> SiteConnectivityRecord | None:
        site = await self.db.get(Site, site_id)
        if site is None:
            return None
        return SiteConnectivityRecord(
            site_id=site.id,
            organization_id=site.organization_id,
            connectivity_mode=str(site.connectivity_mode),
            anpr_deployment_mode=str(site.anpr_deployment_mode),
            primary_gateway_id=site.primary_gateway_id,
        )

    async def save(self, record: SiteConnectivityRecord) -> SiteConnectivityRecord:
        site = await self.db.get(Site, record.site_id)
        if site is None:
            raise ValueError("Site not found")
        site.connectivity_mode = record.connectivity_mode
        site.anpr_deployment_mode = record.anpr_deployment_mode
        site.primary_gateway_id = record.primary_gateway_id
        await self.db.flush()
        return record
