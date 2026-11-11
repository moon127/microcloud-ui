"""Read-only cluster views and live metrics (CF-3, CL-1 basics, MO-1)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from mcui.api.deps import LiveDep, LXDDep, SessionDep
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


@router.get("/storage-pools")
async def storage_pools(_: SessionDep, lxd: LXDDep) -> list[dict[str, Any]]:
    data = await lxd.get("/1.0/storage-pools", recursion=1)
    return [
        {
            "name": p["name"],
            "driver": p.get("driver", ""),
            "status": p.get("status", ""),
            "description": p.get("description", ""),
            "config": p.get("config") or {},
            "used_by": len(p.get("used_by") or []),
            "locations": p.get("locations") or [],
        }
        for p in data or []
    ]
