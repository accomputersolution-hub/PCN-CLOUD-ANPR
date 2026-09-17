from __future__ import annotations

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UnauthorizedError
from app.core.security import decode_token
from app.db.session import get_db
from app.models.enums import UserRole
from app.models.user import User
from app.services.realtime import hub

router = APIRouter(tags=["realtime"])


@router.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> None:
    try:
        payload = decode_token(token, "access")
    except ValueError:
        await ws.close(code=4401)
        return
    user = await db.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        await ws.close(code=4401)
        return
    await hub.connect(ws, user.organization_id, user.role == UserRole.SUPER_ADMIN)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(ws)
