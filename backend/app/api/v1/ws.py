from __future__ import annotations

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UnauthorizedError
from app.core.runtime import is_firestore
from app.core.security import decode_token
from app.db.session import get_db
from app.identity.principal import principal_from_profile
from app.models.enums import UserRole
from app.services.realtime import hub

router = APIRouter(tags=["realtime"])


@router.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    token: str = Query(...),
    db: AsyncSession | None = Depends(get_db),
) -> None:
    try:
        payload = decode_token(token, "access")
    except ValueError:
        await ws.close(code=4401)
        return

    user_id = payload.get("sub")
    if not user_id:
        await ws.close(code=4401)
        return

    if is_firestore():
        from app.services.auth_firestore import load_principal

        try:
            principal = await load_principal(str(user_id))
        except UnauthorizedError:
            await ws.close(code=4401)
            return
    else:
        if db is None:
            await ws.close(code=4401)
            return
        from app.models.user import User

        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            await ws.close(code=4401)
            return
        principal = principal_from_profile(user)

    await hub.connect(
        ws,
        principal.organization_id,
        principal.role_enum == UserRole.SUPER_ADMIN,
    )
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(ws)
