from __future__ import annotations

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.rbac import Permission, has_permission
from app.core.runtime import is_firestore
from app.core.security import decode_token
from app.db.session import get_db
from app.identity.principal import Principal, principal_from_profile
from app.models.enums import UserRole
from app.services.tenant import TenantContext, load_site_ids

bearer = HTTPBearer(auto_error=False)


def _role(user: Principal) -> UserRole:
    return user.role_enum


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession | None = Depends(get_db),
) -> Principal:
    if creds is None or creds.scheme.lower() != "bearer":
        raise UnauthorizedError()
    try:
        payload = decode_token(creds.credentials, "access")
    except ValueError as exc:
        raise UnauthorizedError(str(exc)) from exc
    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError("Invalid token subject")

    if is_firestore():
        from app.services.auth_firestore import load_principal

        return await load_principal(str(user_id))

    if db is None:
        raise UnauthorizedError("Database session unavailable")
    from app.models.user import User

    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("User not found or inactive")
    return principal_from_profile(user)


async def get_tenant(
    user: Principal = Depends(get_current_user),
    db: AsyncSession | None = Depends(get_db),
) -> TenantContext:
    site_ids = await load_site_ids(db, user)
    # Prefer explicit site_ids on the principal when present (Firestore profiles).
    if isinstance(user, Principal) and user.site_ids and not site_ids:
        site_ids = list(user.site_ids)
    return TenantContext(user, site_ids)


def require_permission(*permissions: Permission):
    async def _inner(user: Principal = Depends(get_current_user)) -> Principal:
        role = _role(user)
        for perm in permissions:
            if not has_permission(role, perm):
                raise ForbiddenError(f"Missing permission: {perm}")
        return user

    return _inner


def client_ip(request: Request, forwarded: str | None = Header(default=None, alias="X-Forwarded-For")) -> str | None:
    if isinstance(forwarded, str) and forwarded.strip():
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


async def get_edge_agent(
    x_edge_key: str | None = Header(default=None, alias="X-Edge-Key"),
    x_edge_id: str | None = Header(default=None, alias="X-Edge-Id"),
    db: AsyncSession | None = Depends(get_db),
):
    from app.core.security import verify_password

    if not x_edge_key or not x_edge_id:
        raise UnauthorizedError("Edge credentials required")

    if is_firestore():
        from app.repositories import edge_agent_repo

        agent = await edge_agent_repo().get(x_edge_id)
        if agent is None or not agent.is_active or not verify_password(x_edge_key, agent.agent_key_hash):
            raise UnauthorizedError("Invalid edge agent credentials")
        return agent

    from app.models.edge_agent import EdgeAgent

    if db is None:
        raise UnauthorizedError("Database session unavailable")
    agent = await db.get(EdgeAgent, x_edge_id)
    if agent is None or not verify_password(x_edge_key, agent.agent_key_hash):
        raise UnauthorizedError("Invalid edge agent credentials")
    return agent


async def get_gateway_device(
    x_gateway_key: str | None = Header(default=None, alias="X-Gateway-Key"),
    x_gateway_id: str | None = Header(default=None, alias="X-Gateway-Id"),
    db: AsyncSession | None = Depends(get_db),
):
    from app.services.gateway import authenticate_gateway

    if not x_gateway_key or not x_gateway_id:
        raise UnauthorizedError("Gateway credentials required")
    return await authenticate_gateway(db, x_gateway_id, x_gateway_key)
