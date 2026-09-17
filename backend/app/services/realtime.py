from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from app.core.logging import get_logger

logger = get_logger(__name__)


class RealtimeHub:
    def __init__(self) -> None:
        self._by_org: dict[str, set[WebSocket]] = defaultdict(set)
        self._super: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, organization_id: str | None, is_super: bool) -> None:
        await ws.accept()
        async with self._lock:
            if is_super:
                self._super.add(ws)
            elif organization_id:
                self._by_org[organization_id].add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._super.discard(ws)
            for org in list(self._by_org):
                self._by_org[org].discard(ws)
                if not self._by_org[org]:
                    del self._by_org[org]

    async def publish(self, organization_id: str, event_type: str, payload: dict[str, Any]) -> None:
        message = json.dumps({"type": event_type, "payload": payload})
        async with self._lock:
            targets = set(self._super)
            targets |= self._by_org.get(organization_id, set())
        stale: list[WebSocket] = []
        for ws in targets:
            if ws.client_state != WebSocketState.CONNECTED:
                stale.append(ws)
                continue
            try:
                await ws.send_text(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            await self.disconnect(ws)


hub = RealtimeHub()
