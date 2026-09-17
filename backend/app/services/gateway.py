from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError, UnauthorizedError, ValidationAppError
from app.core.runtime import is_firestore
from app.core.security import hash_password, verify_password
from app.domain.connectivity import GatewayRecord
from app.domain.records import SiteRecord
from app.models.camera import Camera
from app.models.edge_agent import EdgeAgent
from app.models.enums import (
    GatewayDeviceType,
    GatewayHealthStatus,
    GatewayProvisioningStatus,
    VpnStatus,
)
from app.models.nvr import Nvr
from app.models.site import Site
from app.repositories import (
    camera_repo,
    edge_agent_repo,
    gateway_repo,
    nvr_repo,
    site_connectivity_repo,
    site_repo,
)
from app.schemas.connectivity import SiteConnectivityOut, SiteConnectivityUpdate
from app.schemas.gateway import GatewayCreated, GatewayHeartbeatRequest, GatewayOut, GatewayUpdate
from app.services.tenant import TenantContext


SECRET_FIELD_NAMES = frozenset(
    {
        "device_key_hash",
        "password",
        "rtsp_url",
        "private_key",
        "vpn_key",
        "preshared_key",
        "wireguard_private_key",
    }
)


def generate_device_key() -> str:
    return secrets.token_urlsafe(32)


def public_gateway(record: GatewayRecord, *, camera_count: int = 0, nvr_count: int = 0, site_name: str | None = None) -> GatewayOut:
    payload = record.model_dump(exclude={"device_key_hash", "key_rotated_at"})
    payload["revoked"] = record.revoked_at is not None
    payload["camera_count"] = camera_count
    payload["nvr_count"] = nvr_count
    payload["site_name"] = site_name
    out = GatewayOut.model_validate(payload)
    dumped = out.model_dump()
    leaked = SECRET_FIELD_NAMES.intersection(dumped)
    if leaked:
        raise ValidationAppError("Refusing to serialize gateway secrets")
    return out


async def _counts(db: AsyncSession | None, gateway_id: str) -> tuple[int, int]:
    if is_firestore() or db is None:
        gw = await gateway_repo(db).get(gateway_id)
        if not gw:
            return 0, 0
        cameras = await camera_repo(db).list_for_tenant(
            organization_id=gw.organization_id, site_ids=None, site_id=gw.site_id
        )
        nvrs = await nvr_repo(db).list_for_tenant(
            organization_id=gw.organization_id, site_ids=None, site_id=gw.site_id
        )
        return (
            sum(1 for c in cameras if c.gateway_id == gateway_id),
            sum(1 for n in nvrs if n.gateway_id == gateway_id),
        )
    cameras = int((await db.execute(select(func.count(Camera.id)).where(Camera.gateway_id == gateway_id))).scalar_one())
    nvrs = int((await db.execute(select(func.count(Nvr.id)).where(Nvr.gateway_id == gateway_id))).scalar_one())
    return cameras, nvrs


async def _site_name(db: AsyncSession | None, site_id: str) -> str | None:
    if is_firestore() or db is None:
        site = await site_repo(db).get(site_id)
        return site.name if site else None
    site = await db.get(Site, site_id)
    return site.name if site else None


async def load_site_for_ctx(db: AsyncSession | None, ctx: TenantContext, site_id: str) -> Site | SiteRecord:
    if is_firestore() or db is None:
        site = await site_repo(db).get(site_id)
        if not site:
            raise NotFoundError("Site not found")
        ctx.ensure_org(site.organization_id)
        ctx.ensure_site(site.id)
        return site
    site = await db.get(Site, site_id)
    if not site:
        raise NotFoundError("Site not found")
    ctx.ensure_org(site.organization_id)
    ctx.ensure_site(site.id)
    return site


async def create_gateway(
    db: AsyncSession | None,
    ctx: TenantContext,
    *,
    site_id: str,
    name: str,
    device_type: GatewayDeviceType,
    vendor: str,
    model: str,
    firmware_version: str | None,
    lan_subnet: str | None,
    capabilities: dict,
    notes: str,
) -> tuple[GatewayOut, str]:
    site = await load_site_for_ctx(db, ctx, site_id)
    device_key = generate_device_key()
    record = GatewayRecord(
        id=str(uuid4()),
        organization_id=site.organization_id,
        site_id=site.id,
        name=name,
        device_type=str(device_type),
        vendor=vendor or "GENERIC",
        model=model or "",
        firmware_version=firmware_version,
        lan_subnet=lan_subnet,
        capabilities=capabilities or {},
        notes=notes or "",
        device_key_hash=hash_password(device_key),
        provisioning_status=str(GatewayProvisioningStatus.PENDING),
        health_status=str(GatewayHealthStatus.UNKNOWN),
        vpn_status=str(VpnStatus.UNKNOWN),
        is_active=True,
    )
    saved = await gateway_repo(db).add(record)
    out = public_gateway(saved, site_name=site.name)
    return out, device_key


async def get_gateway(db: AsyncSession | None, ctx: TenantContext, gateway_id: str) -> GatewayOut:
    record = await gateway_repo(db).get(gateway_id)
    if record is None:
        raise NotFoundError("Gateway not found")
    ctx.ensure_org(record.organization_id)
    ctx.ensure_site(record.site_id)
    cameras, nvrs = await _counts(db, record.id)
    return public_gateway(record, camera_count=cameras, nvr_count=nvrs, site_name=await _site_name(db, record.site_id))


async def list_gateways(db: AsyncSession | None, ctx: TenantContext, site_id: str | None = None) -> list[GatewayOut]:
    if site_id:
        await load_site_for_ctx(db, ctx, site_id)
    org_id = None if ctx.is_super else ctx.organization_id
    site_ids = ctx.site_ids or None
    records = await gateway_repo(db).list_for_tenant(organization_id=org_id, site_ids=site_ids, site_id=site_id)
    out: list[GatewayOut] = []
    for record in records:
        cameras, nvrs = await _counts(db, record.id)
        out.append(
            public_gateway(
                record,
                camera_count=cameras,
                nvr_count=nvrs,
                site_name=await _site_name(db, record.site_id),
            )
        )
    return out


async def update_gateway(db: AsyncSession | None, ctx: TenantContext, gateway_id: str, body: GatewayUpdate) -> GatewayOut:
    record = await gateway_repo(db).get(gateway_id)
    if record is None:
        raise NotFoundError("Gateway not found")
    ctx.ensure_org(record.organization_id)
    ctx.ensure_site(record.site_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("is_active") is False:
        record.health_status = str(GatewayHealthStatus.DISABLED)
    elif data.get("is_active") is True and record.revoked_at is None:
        if record.health_status == str(GatewayHealthStatus.DISABLED):
            record.health_status = str(GatewayHealthStatus.UNKNOWN)
    for key, value in data.items():
        setattr(record, key, value)
    saved = await gateway_repo(db).save(record)
    cameras, nvrs = await _counts(db, saved.id)
    return public_gateway(saved, camera_count=cameras, nvr_count=nvrs, site_name=await _site_name(db, saved.site_id))


async def decommission_gateway(db: AsyncSession | None, ctx: TenantContext, gateway_id: str) -> None:
    record = await gateway_repo(db).get(gateway_id)
    if record is None:
        raise NotFoundError("Gateway not found")
    ctx.ensure_org(record.organization_id)
    ctx.ensure_site(record.site_id)
    record.is_active = False
    record.revoked_at = datetime.now(UTC)
    record.device_key_hash = None
    record.provisioning_status = str(GatewayProvisioningStatus.REVOKED)
    record.health_status = str(GatewayHealthStatus.REVOKED)
    record.vpn_status = str(VpnStatus.DISCONNECTED)
    await gateway_repo(db).save(record)
    site_conn = await site_connectivity_repo(db).get(record.site_id)
    if site_conn and site_conn.primary_gateway_id == record.id:
        site_conn.primary_gateway_id = None
        await site_connectivity_repo(db).save(site_conn)


async def provision_gateway(db: AsyncSession | None, ctx: TenantContext, gateway_id: str) -> tuple[GatewayOut, str]:
    record = await gateway_repo(db).get(gateway_id)
    if record is None:
        raise NotFoundError("Gateway not found")
    ctx.ensure_org(record.organization_id)
    ctx.ensure_site(record.site_id)
    if record.revoked_at is not None:
        raise ForbiddenError("Gateway is revoked")
    device_key = generate_device_key()
    record.device_key_hash = hash_password(device_key)
    record.key_rotated_at = datetime.now(UTC)
    record.provisioning_status = str(GatewayProvisioningStatus.PROVISIONED)
    record.is_active = True
    record.config_version += 1
    saved = await gateway_repo(db).save(record)
    cameras, nvrs = await _counts(db, saved.id)
    out = public_gateway(saved, camera_count=cameras, nvr_count=nvrs, site_name=await _site_name(db, saved.site_id))
    return out, device_key


async def revoke_gateway(db: AsyncSession | None, ctx: TenantContext, gateway_id: str) -> GatewayOut:
    record = await gateway_repo(db).get(gateway_id)
    if record is None:
        raise NotFoundError("Gateway not found")
    ctx.ensure_org(record.organization_id)
    ctx.ensure_site(record.site_id)
    record.revoked_at = datetime.now(UTC)
    record.device_key_hash = None
    record.is_active = False
    record.provisioning_status = str(GatewayProvisioningStatus.REVOKED)
    record.health_status = str(GatewayHealthStatus.REVOKED)
    record.vpn_status = str(VpnStatus.DISCONNECTED)
    saved = await gateway_repo(db).save(record)
    cameras, nvrs = await _counts(db, saved.id)
    return public_gateway(saved, camera_count=cameras, nvr_count=nvrs, site_name=await _site_name(db, saved.site_id))


async def authenticate_gateway(db: AsyncSession | None, gateway_id: str, device_key: str) -> GatewayRecord:
    record = await gateway_repo(db).get(gateway_id)
    if (
        record is None
        or not record.is_active
        or record.revoked_at is not None
        or not record.device_key_hash
        or not verify_password(device_key, record.device_key_hash)
    ):
        raise UnauthorizedError("Invalid gateway credentials")
    return record


async def apply_heartbeat(db: AsyncSession | None, record: GatewayRecord, body: GatewayHeartbeatRequest) -> GatewayOut:
    now = datetime.now(UTC)
    record.last_seen = now
    if body.vpn_status is not None:
        record.vpn_status = str(body.vpn_status)
    if body.health_status is not None:
        record.health_status = str(body.health_status)
    else:
        record.health_status = str(GatewayHealthStatus.HEALTHY)
    if body.firmware_version is not None:
        record.firmware_version = body.firmware_version
    if body.lan_subnet is not None:
        record.lan_subnet = body.lan_subnet
    if body.cpu_usage is not None:
        record.cpu_usage = body.cpu_usage
    if body.memory_usage is not None:
        record.memory_usage = body.memory_usage
    if body.last_error is not None:
        record.last_error = body.last_error[:500] if body.last_error else None
    if body.capabilities is not None:
        record.capabilities = body.capabilities
    if record.provisioning_status == str(GatewayProvisioningStatus.PENDING):
        record.provisioning_status = str(GatewayProvisioningStatus.PROVISIONED)
    saved = await gateway_repo(db).save(record)
    cameras, nvrs = await _counts(db, saved.id)
    return public_gateway(saved, camera_count=cameras, nvr_count=nvrs, site_name=await _site_name(db, saved.site_id))


async def site_connectivity(db: AsyncSession | None, ctx: TenantContext, site_id: str) -> SiteConnectivityOut:
    site = await load_site_for_ctx(db, ctx, site_id)
    gateway_out = None
    primary_gateway_id = getattr(site, "primary_gateway_id", None)
    if primary_gateway_id:
        gw = await gateway_repo(db).get(primary_gateway_id)
        if gw and gw.site_id == site.id:
            cameras, nvrs = await _counts(db, gw.id)
            gateway_out = public_gateway(gw, camera_count=cameras, nvr_count=nvrs, site_name=site.name)

    if is_firestore() or db is None:
        cams = await camera_repo(db).list_for_tenant(
            organization_id=site.organization_id, site_ids=None, site_id=site.id
        )
        nvrs_list = await nvr_repo(db).list_for_tenant(
            organization_id=site.organization_id, site_ids=None, site_id=site.id
        )
        edges = await edge_agent_repo(db).list_for_tenant(
            organization_id=site.organization_id, site_ids=None, site_id=site.id
        )
        camera_count = len(cams)
        nvr_count = len(nvrs_list)
        edge_count = len(edges)
        connectivity_mode = getattr(site, "connectivity_mode", "EXISTING_VPN_ROUTER")
        anpr_deployment_mode = getattr(site, "anpr_deployment_mode", "LOCAL_EDGE_AGENT")
    else:
        camera_count = int((await db.execute(select(func.count(Camera.id)).where(Camera.site_id == site.id))).scalar_one())
        nvr_count = int((await db.execute(select(func.count(Nvr.id)).where(Nvr.site_id == site.id))).scalar_one())
        edge_count = int((await db.execute(select(func.count(EdgeAgent.id)).where(EdgeAgent.site_id == site.id))).scalar_one())
        connectivity_mode = site.connectivity_mode
        anpr_deployment_mode = site.anpr_deployment_mode

    return SiteConnectivityOut(
        site_id=site.id,
        organization_id=site.organization_id,
        site_name=site.name,
        connectivity_mode=connectivity_mode,
        anpr_deployment_mode=anpr_deployment_mode,
        primary_gateway_id=primary_gateway_id,
        gateway=gateway_out,
        camera_count=camera_count,
        nvr_count=nvr_count,
        edge_agent_count=edge_count,
    )


async def update_site_connectivity(
    db: AsyncSession | None,
    ctx: TenantContext,
    site_id: str,
    body: SiteConnectivityUpdate,
) -> SiteConnectivityOut:
    site = await load_site_for_ctx(db, ctx, site_id)
    data = body.model_dump(exclude_unset=True)
    if "primary_gateway_id" in data and data["primary_gateway_id"]:
        gw = await gateway_repo(db).get(data["primary_gateway_id"])
        if gw is None or gw.site_id != site.id or gw.organization_id != site.organization_id:
            raise ValidationAppError("Gateway does not belong to this site")
        if gw.revoked_at is not None:
            raise ValidationAppError("Cannot assign a revoked gateway")
    conn = await site_connectivity_repo(db).get(site.id)
    if conn is None:
        raise NotFoundError("Site not found")
    if "connectivity_mode" in data and data["connectivity_mode"] is not None:
        conn.connectivity_mode = str(data["connectivity_mode"])
    if "anpr_deployment_mode" in data and data["anpr_deployment_mode"] is not None:
        conn.anpr_deployment_mode = str(data["anpr_deployment_mode"])
    if "primary_gateway_id" in data:
        conn.primary_gateway_id = data["primary_gateway_id"]
    await site_connectivity_repo(db).save(conn)
    return await site_connectivity(db, ctx, site.id)


def created_payload(out: GatewayOut, device_key: str) -> GatewayCreated:
    return GatewayCreated(**out.model_dump(), device_key=device_key)
