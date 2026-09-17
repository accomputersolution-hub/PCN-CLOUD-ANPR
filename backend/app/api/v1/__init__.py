from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from app.api.deps import get_current_user
from app.api.v1 import (
    anpr,
    auth,
    cameras,
    dashboard,
    edge,
    events,
    gates,
    health,
    mock,
    ops,
    organizations,
    reports,
    sites,
    users,
    vehicles,
    ws,
)
from app.core.rbac import Permission, has_permission
from app.models.user import User
from app.services.storage import get_storage

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(dashboard.router)
api_router.include_router(organizations.router)
api_router.include_router(sites.router)
api_router.include_router(gates.router)
api_router.include_router(cameras.router)
api_router.include_router(events.router)
api_router.include_router(vehicles.router)
api_router.include_router(users.router)
api_router.include_router(edge.router)
api_router.include_router(reports.router)
api_router.include_router(mock.router)
api_router.include_router(anpr.router)
api_router.include_router(ops.router)
api_router.include_router(ws.router)


@api_router.get("/storage/{key:path}", include_in_schema=False)
async def get_stored_object(key: str, user: User = Depends(get_current_user)) -> Response:
    if not has_permission(user.role, Permission.EVENT_READ):
        raise HTTPException(status_code=403, detail="Forbidden")
    if not user.role.value == "SUPER_ADMIN" and user.organization_id and not key.startswith(user.organization_id):
        raise HTTPException(status_code=403, detail="Forbidden")
    data = get_storage().get_bytes(key)
    if data is None:
        raise HTTPException(status_code=404, detail="Not found")
    return Response(content=data, media_type="image/jpeg")
