"""Terminal, text console and VGA console (IN-7, IN-8, IN-9).

The backend starts the LXD exec/console operation, keeps its websocket secrets
server-side, and lets the user who started it attach through ``/api/streams/{id}``:
binary frames are the session's data, text frames are LXD control messages
(window-resize). The backend itself holds LXD's control channel while any browser
connection is attached, because LXD waits for every channel before it proceeds
(seen live: a missing control channel left exec pending and broke a later stop).
SPICE (VGA) opens several data connections (main, display, inputs, …) to the same
stream; each is proxied and they share one control channel. The browser never
talks to LXD or sees a secret (D4, D25, D48).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response, WebSocket
from pydantic import BaseModel, Field
from websockets.asyncio.client import ClientConnection

from mcui.api.audited import audited
from mcui.api.deps import AdminDep, LXDDep, ServicesDep, SessionDep, StoreDep
from mcui.api.instances import InstName, ProjectName
from mcui.api.wsproxy import POLICY, UPSTREAM, authenticate, relay_stream
from mcui.lxd import operations
from mcui.lxd.client import LXDClient, LXDError
from mcui.lxd.server import members
from mcui.services import Services
from mcui.streams import Stream, StreamRegistry

router = APIRouter(prefix="/api")

DEFAULT_SHELL = [
    "sh",
    "-c",
    "if command -v bash >/dev/null; then exec bash -l; else exec sh -l; fi",
]


def get_registry(services: ServicesDep) -> StreamRegistry:
    return services.streams


RegistryDep = Annotated[StreamRegistry, Depends(get_registry)]


async def member_url(lxd: LXDClient, location: str | None) -> str | None:
    """The API URL of the member running an operation (its websockets live there)."""
    if not location or location == "none":
        return None
    for m in await members(lxd):
        if m.name == location:
            return m.url
    return None


async def _start(
    action: str,
    kind: str,
    path: str,
    payload: dict[str, Any],
    project: str,
    name: str,
    session: Any,
    store: Any,
    lxd: LXDClient,
    registry: StreamRegistry,
    detail: dict[str, Any],
) -> dict[str, Any]:
    st = await audited(
        session,
        store,
        action,
        f"{project}/{name}",
        operations.start(lxd, "POST", path, json=payload, params={"project": project}),
        detail=detail,
    )
    op = st.operation or {}
    fds = (op.get("metadata") or {}).get("fds") or {}
    if "0" not in fds:
        raise LXDError("LXD did not return a websocket for this session", 502)
    stream = Stream(
        id=uuid.uuid4().hex,
        kind=kind,
        owner=session.user.username,
        project=project,
        instance=name,
        operation=str(op.get("id")),
        base=await member_url(lxd, op.get("location")),
        secrets={"data": fds["0"], **({"control": fds["control"]} if "control" in fds else {})},
    )
    registry.add(stream)
    return {"id": stream.id, "kind": kind, "operation": stream.operation}


class ExecRequest(BaseModel):
    command: list[str] = Field(default_factory=lambda: list(DEFAULT_SHELL))
    cols: int = Field(default=80, ge=10, le=1000)
    rows: int = Field(default=24, ge=5, le=500)


@router.post("/instances/{project}/{name}/exec")
async def start_exec(
    project: ProjectName,
    name: InstName,
    body: ExecRequest,
    session: AdminDep,
    store: StoreDep,
    lxd: LXDDep,
    registry: RegistryDep,
) -> dict[str, Any]:
    payload = {
        "command": body.command,
        "environment": {"TERM": "xterm-256color", "HOME": "/root"},
        "interactive": True,
        "wait-for-websocket": True,
        "width": body.cols,
        "height": body.rows,
    }
    return await _start(
        "instance.exec",
        "exec",
        f"/1.0/instances/{name}/exec",
        payload,
        project,
        name,
        session,
        store,
        lxd,
        registry,
        {"command": body.command},
    )


class ConsoleRequest(BaseModel):
    type: Literal["console", "vga"] = "console"
    cols: int = Field(default=80, ge=10, le=1000)
    rows: int = Field(default=24, ge=5, le=500)


@router.post("/instances/{project}/{name}/console")
async def start_console(
    project: ProjectName,
    name: InstName,
    body: ConsoleRequest,
    session: AdminDep,
    store: StoreDep,
    lxd: LXDDep,
    registry: RegistryDep,
) -> dict[str, Any]:
    payload = {"type": body.type, "width": body.cols, "height": body.rows}
    kind = "vga" if body.type == "vga" else "console"
    return await _start(
        "instance.console",
        kind,
        f"/1.0/instances/{name}/console",
        payload,
        project,
        name,
        session,
        store,
        lxd,
        registry,
        {"type": body.type},
    )


@router.get("/instances/{project}/{name}/console-log")
async def console_log(project: ProjectName, name: InstName, _: SessionDep, lxd: LXDDep) -> Response:
    r = await lxd.raw("GET", f"/1.0/instances/{name}/console", params={"project": project})
    if r.status_code != 200:
        msg = (
            r.json().get("error")
            if r.headers.get("content-type", "").startswith("application/json")
            else None
        )
        return Response(
            f"LXD: {msg or f'HTTP {r.status_code}'}", status_code=502, media_type="text/plain"
        )
    return Response(r.content, media_type="text/plain; charset=utf-8")


@router.delete("/instances/{project}/{name}/console-log")
async def clear_console_log(
    project: ProjectName, name: InstName, session: AdminDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    st = await audited(
        session,
        store,
        "instance.console-log.clear",
        f"{project}/{name}",
        operations.start(
            lxd, "DELETE", f"/1.0/instances/{name}/console", params={"project": project}
        ),
    )
    return st.as_dict()


@router.websocket("/streams/{stream_id}")
async def attach(ws: WebSocket, stream_id: str) -> None:
    session = await authenticate(ws, admin=True, subprotocols=("binary",))
    if session is None:
        return
    services: Services = ws.app.state.services
    stream = services.streams.get(stream_id)
    if stream is None or stream.owner != session.user.username or services.lxd is None:
        await ws.close(code=POLICY, reason="no such session")
        return
    lxd = services.lxd
    path = f"/1.0/operations/{stream.operation}/websocket"

    async def connect(secret: str) -> ClientConnection:
        return await lxd.websocket(path, {"secret": secret}, base=stream.base)

    try:
        control = await stream.acquire_control(connect)
    except LXDError as e:
        await stream.release_control()
        await ws.close(code=UPSTREAM, reason=str(e)[:120])
        return
    try:
        data = await connect(stream.secrets["data"])
    except LXDError as e:
        await stream.release_control()
        await ws.close(code=UPSTREAM, reason=str(e)[:120])
        return
    try:
        await relay_stream(ws, data, control)
    finally:
        await stream.release_control()
