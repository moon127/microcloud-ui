"""Read-only cluster views and live metrics (CF-3, CL-1 basics, MO-1)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from mcui.api.deps import LiveDep, LXDDep, ServicesDep, SessionDep
from mcui.lxd.instances import InstanceIndex
from mcui.lxd.server import members, server_info

router = APIRouter(prefix="/api")


@router.get("/server")
async def server(_: SessionDep, lxd: LXDDep) -> dict[str, Any]:
    info = await server_info(lxd)
    return {**info.as_dict(), "endpoint": lxd.endpoint}


@router.get("/cluster/members")
async def cluster_members(_: SessionDep, lxd: LXDDep) -> list[dict[str, Any]]:
    return [m.as_dict() for m in await members(lxd)]


@router.get("/metrics/live")
async def live_metrics(_: SessionDep, live: LiveDep) -> dict[str, Any]:
    return await live.snapshot()


@router.get("/cluster/members/{name}/resources")
async def member_resources(name: str, _: SessionDep, lxd: LXDDep) -> dict[str, Any]:
    """Hardware inventory of one member (CL-2)."""
    if name not in {m.name for m in await members(lxd)}:
        raise HTTPException(status_code=404, detail="no such member")
    data: dict[str, Any] = await lxd.get("/1.0/resources", target=name)
    return data


async def get_index(services: ServicesDep) -> InstanceIndex:
    if services.instances is None:
        raise HTTPException(status_code=503, detail="LXD is not configured")
    return services.instances


IndexDep = Annotated[InstanceIndex, Depends(get_index)]


@router.get("/instances")
async def instances(_: SessionDep, index: IndexDep) -> list[dict[str, Any]]:
    return [i.as_dict() for i in await index.get()]


@router.get("/instances/{project}/{name}")
async def instance(project: str, name: str, _: SessionDep, index: IndexDep) -> dict[str, Any]:
    for i in await index.get():
        if (i.project, i.name) == (project, name):
            return i.as_dict()
    raise HTTPException(status_code=404, detail="no such instance")
