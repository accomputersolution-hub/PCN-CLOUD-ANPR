from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.api.deps import get_current_user
from app.api.v1 import (
    anpr,
    auth,
    camera_calibration,
    cameras,
    dashboard,
    edge,
    events,
    gates,
    gateways,
    health,
    manual_anpr,
    mock,
    nvrs,
    ops,
    organizations,
    reports,
    sites,
    users,
    vehicle_registry,
    vehicles,
    ws,
)
from app.services.storage import get_storage

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(organizations.router)
api_router.include_router(sites.router)
api_router.include_router(vehicle_registry.router)
api_router.include_router(gates.router)
api_router.include_router(cameras.router)
api_router.include_router(camera_calibration.router)
api_router.include_router(gateways.router)
api_router.include_router(nvrs.router)
api_router.include_router(events.router)
api_router.include_router(vehicles.router)
api_router.include_router(users.router)
api_router.include_router(edge.router)
api_router.include_router(reports.router)
api_router.include_router(mock.router)
api_router.include_router(manual_anpr.router)
api_router.include_router(anpr.router)
api_router.include_router(ops.router)
api_router.include_router(ws.router)


@api_router.get("/storage/{key:path}", include_in_schema=False)
async def get_stored_object(key: str, user=Depends(get_current_user)) -> Response:
    from app.core.rbac import Permission, has_permission
    from app.identity.principal import Principal
    from app.models.enums import UserRole

    role = user.role_enum if isinstance(user, Principal) else user.role
    if isinstance(role, str):
        role = UserRole(role)
    if not has_permission(role, Permission.EVENT_READ):
        raise HTTPException(status_code=403, detail="Forbidden")
    org_id = user.organization_id
    if role != UserRole.SUPER_ADMIN and org_id and not key.startswith(str(org_id)):
        raise HTTPException(status_code=403, detail="Forbidden")
    data = get_storage().get_bytes(key)
    if data is None:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(content=data, media_type="image/jpeg")
