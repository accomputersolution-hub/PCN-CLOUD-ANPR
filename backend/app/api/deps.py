from __future__ import annotations

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.rbac import Permission, has_permission
from app.core.security import decode_token
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.services.tenant import TenantContext, load_site_ids

bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if creds is None or creds.scheme.lower() != "bearer":
        raise UnauthorizedError()
    try:
        payload = decode_token(creds.credentials, "access")
    except ValueError as exc:
        raise UnauthorizedError(str(exc)) from exc
    user = await db.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise UnauthorizedError("User not found or inactive")
    return user


async def get_tenant(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TenantContext:
    site_ids = await load_site_ids(db, user)
    return TenantContext(user, site_ids)


def require_permission(*permissions: Permission):
    async def _inner(user: User = Depends(get_current_user)) -> User:
        for perm in permissions:
            if not has_permission(user.role, perm):
                raise ForbiddenError(f"Missing permission: {perm}")
        return user

    return _inner


def client_ip(request: Request, forwarded: str | None = Header(default=None, alias="X-Forwarded-For")) -> str | None:
    # Callers may invoke this as a plain helper (client_ip(request)); in that case
    # FastAPI's Header() default object is passed and must be ignored.
    if isinstance(forwarded, str) and forwarded.strip():
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


async def get_edge_agent(
    x_edge_key: str | None = Header(default=None, alias="X-Edge-Key"),
    x_edge_id: str | None = Header(default=None, alias="X-Edge-Id"),
    db: AsyncSession = Depends(get_db),
):
    from app.core.security import verify_password
    from app.models.edge_agent import EdgeAgent

    if not x_edge_key or not x_edge_id:
        raise UnauthorizedError("Edge credentials required")
    agent = await db.get(EdgeAgent, x_edge_id)
    if agent is None or not verify_password(x_edge_key, agent.agent_key_hash):
        raise UnauthorizedError("Invalid edge agent credentials")
    return agent


async def get_gateway_device(
    x_gateway_key: str | None = Header(default=None, alias="X-Gateway-Key"),
    x_gateway_id: str | None = Header(default=None, alias="X-Gateway-Id"),
    db: AsyncSession = Depends(get_db),
):
    from app.services.gateway import authenticate_gateway

    if not x_gateway_key or not x_gateway_id:
        raise UnauthorizedError("Gateway credentials required")
    return await authenticate_gateway(db, x_gateway_id, x_gateway_key)
