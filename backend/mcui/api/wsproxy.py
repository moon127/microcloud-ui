"""Browser websocket <-> LXD websocket relay, with cookie auth and an Origin check."""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from collections.abc import Callable, Coroutine
from typing import Any
from urllib.parse import urlsplit

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import ConnectionClosed

from mcui.api.deps import COOKIE
from mcui.auth.store import Session
from mcui.services import Services

# Close codes in the private 4000-4999 range
POLICY = 4403
UNAUTHORIZED = 4401
UPSTREAM = 4502


def same_origin(ws: WebSocket) -> bool:
    """The browser's Origin must name this host (blocks cross-site websocket hijacking)."""
    origin = ws.headers.get("origin")
    host = ws.headers.get("host")
    if not origin or not host:
        return False
    return urlsplit(origin).netloc == host


async def authenticate(
    ws: WebSocket, *, admin: bool = False, subprotocols: tuple[str, ...] = ()
) -> Session | None:
    """Accept the socket and return the session, or close it with a reason and return ``None``.

    Accepting first lets the browser see the close code (4401/4403) instead of a
    bare failed handshake.
    """
    services: Services = ws.app.state.services
    offered = ws.scope.get("subprotocols") or []
    chosen = next((p for p in subprotocols if p in offered), None)
    await ws.accept(subprotocol=chosen)
    if not same_origin(ws):
        await ws.close(code=POLICY, reason="cross-origin websocket refused")
        return None
    token = ws.cookies.get(COOKIE)
    session = services.store.get_session(token) if token else None
    if session is None:
        await ws.close(code=UNAUTHORIZED, reason="not logged in")
        return None
    if admin and not session.user.is_admin:
        await ws.close(code=POLICY, reason="admin role required")
        return None
    return session


class Dedupe:
    """Drops frames identical to one of the last ``size`` frames.

    A clustered LXD sends every event twice on ``/1.0/events`` (same timestamp,
    member and payload), so the events relay filters exact repeats.
    """

    def __init__(self, size: int = 256) -> None:
        self._order: deque[str | bytes] = deque(maxlen=size)
        self._seen: set[str | bytes] = set()

    def __call__(self, msg: str | bytes) -> bool:
        if msg in self._seen:
            return False
        if len(self._order) == self._order.maxlen:
            self._seen.discard(self._order[0])
        self._order.append(msg)
        self._seen.add(msg)
        return True


async def relay_stream(
    client: WebSocket, data: ClientConnection, control: ClientConnection | None
) -> None:
    """Console/terminal relay: browser binary frames go to LXD's data channel, browser
    text frames (JSON such as window-resize) to the control channel."""

    async def down() -> None:
        async for msg in data:
            if isinstance(msg, bytes):
                await client.send_bytes(msg)
            else:
                await client.send_text(msg)

    async def up() -> None:
        while True:
            msg = await client.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                await data.send(msg["bytes"])
            elif msg.get("text") is not None and control is not None:
                await control.send(msg["text"])

    await _pump(client, data, down, up)


async def _pump(
    client: WebSocket,
    upstream: ClientConnection,
    down: Callable[[], Coroutine[Any, Any, None]],
    up: Callable[[], Coroutine[Any, Any, None]],
) -> None:
    tasks = [asyncio.create_task(down()), asyncio.create_task(up())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(
                asyncio.CancelledError, ConnectionClosed, WebSocketDisconnect, RuntimeError, OSError
            ):
                await t
    finally:
        await upstream.close()
        if client.client_state == WebSocketState.CONNECTED:
            # the browser may vanish between the check and the close
            with contextlib.suppress(RuntimeError, OSError):
                await client.close()


async def relay(
    client: WebSocket,
    upstream: ClientConnection,
    *,
    read_client: bool = True,
    keep: Callable[[str | bytes], bool] | None = None,
) -> None:
    """Pump frames both ways until either side closes. Text stays text, binary stays binary.

    ``keep`` filters frames going to the browser.
    """

    async def down() -> None:
        async for msg in upstream:
            if keep is not None and not keep(msg):
                continue
            if isinstance(msg, bytes):
                await client.send_bytes(msg)
            else:
                await client.send_text(msg)

    async def up() -> None:
        while True:
            msg = await client.receive()
            if msg["type"] == "websocket.disconnect":
                return
            if not read_client:
                continue
            if msg.get("bytes") is not None:
                await upstream.send(msg["bytes"])
            elif msg.get("text") is not None:
                await upstream.send(msg["text"])

    await _pump(client, upstream, down, up)
