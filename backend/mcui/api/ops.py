"""Operations, events, warnings, member evacuation, project list (CL-3, CL-7, CL-8)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, WebSocket
from pydantic import BaseModel

from mcui.api.audited import audited
from mcui.api.deps import AdminDep, LXDDep, SessionDep, StoreDep
from mcui.api.wsproxy import UPSTREAM, Dedupe, authenticate, relay
from mcui.lxd import operations
from mcui.lxd.client import LXDError
from mcui.lxd.server import members
from mcui.services import Services

router = APIRouter(prefix="/api")


@router.get("/operations")
async def list_operations(_: SessionDep, lxd: LXDDep) -> list[dict[str, Any]]:
    return await operations.list_all(lxd)


@router.get("/operations/{op_id}")
async def get_operation(op_id: str, _: SessionDep, lxd: LXDDep, wait: int = 0) -> dict[str, Any]:
    """With ``wait`` > 0, block up to that many seconds (max 30) for completion."""
    if wait > 0:
        return await operations.wait(lxd, op_id, wait)
    return await operations.get(lxd, op_id)


@router.delete("/operations/{op_id}", status_code=204)
async def cancel_operation(op_id: str, session: AdminDep, store: StoreDep, lxd: LXDDep) -> None:
    async def call() -> operations.Started:
        await operations.cancel(lxd, op_id)
        return operations.Started(operation={"id": op_id})

    await audited(session, store, "operation.cancel", op_id, call())


@router.get("/warnings")
async def list_warnings(_: SessionDep, lxd: LXDDep) -> list[dict[str, Any]]:
    data = await lxd.get("/1.0/warnings", recursion=1, **{"all-projects": "true"})
    return sorted(data or [], key=lambda w: str(w.get("last_seen_at", "")), reverse=True)


class WarningChange(BaseModel):
    status: Literal["new", "acknowledged"]


@router.put("/warnings/{uuid}")
async def update_warning(
    uuid: str, body: WarningChange, session: AdminDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    st = await audited(
        session,
        store,
        "warning.update",
        uuid,
        operations.start(lxd, "PUT", f"/1.0/warnings/{uuid}", json={"status": body.status}),
        detail={"status": body.status},
    )
    return st.as_dict()


@router.delete("/warnings/{uuid}", status_code=204)
async def delete_warning(uuid: str, session: AdminDep, store: StoreDep, lxd: LXDDep) -> None:
    await audited(
        session,
        store,
        "warning.delete",
        uuid,
        operations.start(lxd, "DELETE", f"/1.0/warnings/{uuid}"),
    )


class Evacuation(BaseModel):
    # LXD takes stop, migrate or live-migrate here; "auto" is only an instance's
    # cluster.evacuate value (LXD 6.10 refuses it, after marking the member evacuated)
    mode: Literal["migrate", "live-migrate", "stop"] | None = None
    # LXD 6.9+ (clustering_evacuation_force): evacuate even if the database loses quorum
    force: bool = False


async def _member_state(
    name: str, action: str, body: Evacuation, session: Any, store: Any, lxd: Any
) -> dict[str, Any]:
    if name not in {m.name for m in await members(lxd)}:
        raise HTTPException(status_code=404, detail="no such member")
    payload: dict[str, Any] = {"action": action}
    if body.mode:
        payload["mode"] = body.mode
    if body.force:
        payload["force"] = True
    detail = {k: v for k, v in (("mode", body.mode), ("force", body.force)) if v}
    st = await audited(
        session,
        store,
        f"member.{action}",
        name,
        operations.start(lxd, "POST", f"/1.0/cluster/members/{name}/state", json=payload),
        detail=detail or None,
    )
    return st.as_dict()


@router.post("/cluster/members/{name}/evacuate")
async def evacuate(
    name: str, body: Evacuation, session: AdminDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    return await _member_state(name, "evacuate", body, session, store, lxd)


@router.post("/cluster/members/{name}/restore")
async def restore(
    name: str, body: Evacuation, session: AdminDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    return await _member_state(name, "restore", body, session, store, lxd)


@router.get("/projects")
async def list_projects(_: SessionDep, lxd: LXDDep) -> list[dict[str, Any]]:
    data = await lxd.get("/1.0/projects", recursion=1)
    return [
        {
            "name": p["name"],
            "description": p.get("description", ""),
            "config": p.get("config") or {},
            "used_by": p.get("used_by") or [],
        }
        for p in data or []
    ]


@router.websocket("/events")
async def events(ws: WebSocket) -> None:
    """Relay LXD operation and lifecycle events from all projects to the browser."""
    if await authenticate(ws) is None:
        return
    services: Services = ws.app.state.services
    if services.lxd is None:
        await ws.close(code=UPSTREAM, reason="LXD is not configured")
        return
    try:
        upstream = await services.lxd.websocket(
            "/1.0/events", {"type": "operation,lifecycle", "all-projects": "true"}
        )
    except LXDError as e:
        await ws.close(code=UPSTREAM, reason=str(e)[:120])
        return
    await relay(ws, upstream, read_client=False, keep=Dedupe())
