"""Site vehicle registry API (Feature 3)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import client_ip, get_current_user, get_db, get_tenant, require_permission
from app.core.rbac import Permission
from app.models.enums import AuditAction
from app.schemas.vehicle_registry import (
    RegistryMatchOut,
    VehicleRegistryCreate,
    VehicleRegistryOut,
    VehicleRegistryUpdate,
)
from app.services import vehicle_registry as reg_svc
from app.services.audit import write_audit
from app.services.tenant import TenantContext

router = APIRouter(prefix="/sites", tags=["vehicle-registry"])


@router.get("/{site_id}/vehicle-registry", response_model=list[VehicleRegistryOut])
async def list_vehicle_registry(
    site_id: str,
    q: str | None = Query(default=None),
    plate: str | None = Query(default=None),
    name: str | None = Query(default=None),
    flat_room_unit: str | None = Query(default=None),
    category: str | None = Query(default=None),
    active: bool | None = Query(default=None),
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    user: Any = Depends(get_current_user),
    _: object = Depends(require_permission(Permission.VEHICLE_READ)),
) -> list[VehicleRegistryOut]:
    rows = await reg_svc.list_registrations(
        db=db,
        ctx=ctx,
        user=user,
        site_id=site_id,
        q=q,
        plate=plate,
        name=name,
        flat_room_unit=flat_room_unit,
        category=category,
        active=active,
    )
    return [VehicleRegistryOut.model_validate(r) for r in rows]


@router.get("/{site_id}/vehicle-registry/lookup", response_model=RegistryMatchOut)
async def lookup_vehicle_registry(
    site_id: str,
    plate: str = Query(...),
    ocr_confidence: float | None = Query(default=None),
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    user: Any = Depends(get_current_user),
    _: object = Depends(require_permission(Permission.VEHICLE_READ)),
) -> RegistryMatchOut:
    match = await reg_svc.lookup_plate(
        db=db,
        ctx=ctx,
        user=user,
        site_id=site_id,
        plate=plate,
        ocr_confidence=ocr_confidence,
    )
    return RegistryMatchOut.model_validate(match)


@router.post("/{site_id}/vehicle-registry", response_model=VehicleRegistryOut, status_code=201)
async def create_vehicle_registry(
    site_id: str,
    body: VehicleRegistryCreate,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    user: Any = Depends(get_current_user),
    _: object = Depends(require_permission(Permission.VEHICLE_REGISTRY_WRITE)),
    ip: str | None = Depends(client_ip),
) -> VehicleRegistryOut:
    row = await reg_svc.create_registration(db=db, ctx=ctx, user=user, site_id=site_id, body=body)
    await write_audit(
        db,
        organization_id=row.get("organization_id"),
        user_id=getattr(user, "id", None),
        action=AuditAction.VEHICLE_REGISTRY_CREATE,
        target_type="vehicle_registry",
        target_id=row.get("id"),
        ip=ip,
        extra={"site_id": site_id, "plate": row.get("plate_normalized"), "category": row.get("category")},
    )
    if db is not None:
        await db.commit()
    return VehicleRegistryOut.model_validate(row)


@router.get("/{site_id}/vehicle-registry/{registration_id}", response_model=VehicleRegistryOut)
async def get_vehicle_registry(
    site_id: str,
    registration_id: str,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    user: Any = Depends(get_current_user),
    _: object = Depends(require_permission(Permission.VEHICLE_READ)),
) -> VehicleRegistryOut:
    row = await reg_svc.get_registration(
        db=db, ctx=ctx, user=user, site_id=site_id, registration_id=registration_id
    )
    return VehicleRegistryOut.model_validate(row)


@router.patch("/{site_id}/vehicle-registry/{registration_id}", response_model=VehicleRegistryOut)
async def patch_vehicle_registry(
    site_id: str,
    registration_id: str,
    body: VehicleRegistryUpdate,
    request: Request,
    db: AsyncSession | None = Depends(get_db),
    ctx: TenantContext = Depends(get_tenant),
    user: Any = Depends(get_current_user),
    _: object = Depends(require_permission(Permission.VEHICLE_REGISTRY_WRITE)),
    ip: str | None = Depends(client_ip),
) -> VehicleRegistryOut:
    row, disabled = await reg_svc.update_registration(
        db=db,
        ctx=ctx,
        user=user,
        site_id=site_id,
        registration_id=registration_id,
        body=body,
    )
    action = AuditAction.VEHICLE_REGISTRY_DISABLE if disabled else AuditAction.VEHICLE_REGISTRY_UPDATE
    await write_audit(
        db,
        organization_id=row.get("organization_id"),
        user_id=getattr(user, "id", None),
        action=action,
        target_type="vehicle_registry",
        target_id=row.get("id"),
        ip=ip,
        extra={"site_id": site_id, "plate": row.get("plate_normalized"), "disabled": disabled},
    )
    if db is not None:
        await db.commit()
    return VehicleRegistryOut.model_validate(row)
