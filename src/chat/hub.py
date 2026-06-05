"""Tracks live WebSocket connections per thread_id and broadcasts messages.

Connection lifecycle is owned by the route handler — this class only
maintains the set of connections and fans out messages. A failed send
during broadcast is swallowed; the next `WebSocketDisconnect` raised on
that socket will reach the handler, which calls `disconnect()` to clean up.
"""

from contextlib import suppress

from fastapi import WebSocket


class ChatHub:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}

    async def connect(self, thread_id: str, websocket: WebSocket) -> None:
        self._connections.setdefault(thread_id, set()).add(websocket)

    async def disconnect(self, thread_id: str, websocket: WebSocket) -> None:
        peers = self._connections.get(thread_id)
        if peers is None:
            return
        peers.discard(websocket)
        if not peers:
            del self._connections[thread_id]

    async def broadcast(self, thread_id: str, payload: dict) -> None:
        # Connection death mid-broadcast is swallowed; the handler's
        # WebSocketDisconnect path will remove it on the next receive loop.
        for ws in list(self._connections.get(thread_id, set())):
            with suppress(Exception):
                await ws.send_json(payload)
