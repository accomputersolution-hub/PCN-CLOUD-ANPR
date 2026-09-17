from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_db, get_gateway_device as require_gateway_device, get_tenant, require_permission
from app.core.rbac import Permission
from app.domain.connectivity import GatewayRecord
from app.models.enums import AuditAction
from app.schemas.gateway import (
    GatewayCreate,
    GatewayCreated,
    GatewayHeartbeatRequest,
    GatewayOut,
    GatewayUpdate,
)
from app.services.audit import write_audit
from app.services.gateway import (
    apply_heartbeat,
    create_gateway,
    created_payload,
    decommission_gateway,
    get_gateway,
    list_gateways,
    provision_gateway,
    revoke_gateway,
    update_gateway,
)
from app.services.realtime import hub
from app.services.tenant import TenantContext

router = APIRouter(prefix="/gateways", tags=["gateways"])


@router.get("", response_model=list[GatewayOut])
async def list_gateway_devices(
    site_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.GATEWAY_READ)),
) -> list[GatewayOut]:
    return await list_gateways(db, ctx, site_id)


@router.post("", response_model=GatewayCreated, status_code=201)
async def create_gateway_device(
    body: GatewayCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.GATEWAY_WRITE)),
) -> GatewayCreated:
    out, device_key = await create_gateway(
        db,
        ctx,
        site_id=body.site_id,
        name=body.name,
        device_type=body.device_type,
        vendor=body.vendor,
        model=body.model,
        firmware_version=body.firmware_version,
        lan_subnet=body.lan_subnet,
        capabilities=body.capabilities,
        notes=body.notes,
    )
    await write_audit(
        db,
        action=AuditAction.GATEWAY_CREATE,
        user_id=ctx.user.id,
        organization_id=out.organization_id,
        ip=client_ip(request),
        target_type="gateway",
        target_id=out.id,
        extra={"device_type": out.device_type, "vendor": out.vendor, "model": out.model},
    )
    await db.commit()
    await hub.publish(out.organization_id, "gateway.status", {"id": out.id, "health_status": out.health_status})
    return created_payload(out, device_key)


@router.get("/{gateway_id}", response_model=GatewayOut)
async def read_gateway_device(
    gateway_id: str,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.GATEWAY_READ)),
) -> GatewayOut:
    return await get_gateway(db, ctx, gateway_id)


@router.patch("/{gateway_id}", response_model=GatewayOut)
async def patch_gateway_device(
    gateway_id: str,
    body: GatewayUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.GATEWAY_WRITE)),
) -> GatewayOut:
    out = await update_gateway(db, ctx, gateway_id, body)
    await write_audit(
        db,
        action=AuditAction.GATEWAY_UPDATE,
        user_id=ctx.user.id,
        organization_id=out.organization_id,
        ip=client_ip(request),
        target_type="gateway",
        target_id=out.id,
    )
    await db.commit()
    await hub.publish(out.organization_id, "gateway.status", {"id": out.id, "health_status": out.health_status})
    return out


@router.delete("/{gateway_id}", status_code=204, response_class=Response)
async def delete_gateway_device(
    gateway_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.GATEWAY_WRITE)),
) -> Response:
    existing = await get_gateway(db, ctx, gateway_id)
    await decommission_gateway(db, ctx, gateway_id)
    await write_audit(
        db,
        action=AuditAction.GATEWAY_DELETE,
        user_id=ctx.user.id,
        organization_id=existing.organization_id,
        ip=client_ip(request),
        target_type="gateway",
        target_id=gateway_id,
    )
    await db.commit()
    await hub.publish(existing.organization_id, "gateway.status", {"id": gateway_id, "health_status": "REVOKED"})
    return Response(status_code=204)


@router.post("/{gateway_id}/provision", response_model=GatewayCreated)
async def provision_gateway_device(
    gateway_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.GATEWAY_PROVISION)),
) -> GatewayCreated:
    out, device_key = await provision_gateway(db, ctx, gateway_id)
    await write_audit(
        db,
        action=AuditAction.GATEWAY_PROVISION,
        user_id=ctx.user.id,
        organization_id=out.organization_id,
        ip=client_ip(request),
        target_type="gateway",
        target_id=out.id,
        extra={"config_version": out.config_version},
    )
    await db.commit()
    return created_payload(out, device_key)


@router.post("/{gateway_id}/revoke", response_model=GatewayOut)
async def revoke_gateway_device(
    gateway_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    _: object = Depends(require_permission(Permission.GATEWAY_PROVISION)),
) -> GatewayOut:
    out = await revoke_gateway(db, ctx, gateway_id)
    await write_audit(
        db,
        action=AuditAction.GATEWAY_REVOKE,
        user_id=ctx.user.id,
        organization_id=out.organization_id,
        ip=client_ip(request),
        target_type="gateway",
        target_id=out.id,
    )
    await db.commit()
    await hub.publish(out.organization_id, "gateway.status", {"id": out.id, "health_status": out.health_status})
    return out


@router.post("/{gateway_id}/heartbeat", response_model=GatewayOut)
async def gateway_heartbeat(
    gateway_id: str,
    body: GatewayHeartbeatRequest,
    db: AsyncSession = Depends(get_db),
    device: GatewayRecord = Depends(require_gateway_device),
) -> GatewayOut:
    if device.id != gateway_id:
        from app.core.exceptions import ForbiddenError

        raise ForbiddenError("Gateway id mismatch")
    out = await apply_heartbeat(db, device, body)
    await db.commit()
    await hub.publish(
        out.organization_id,
        "gateway.status",
        {"id": out.id, "health_status": out.health_status, "vpn_status": out.vpn_status},
    )
    return out
