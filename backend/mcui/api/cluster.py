"""Read-only cluster views and live metrics (CF-3, CL-1 basics, MO-1)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from mcui.api.deps import ClusterReadDep, LiveDep, LXDDep, ScopeDep, SessionDep
from mcui.lxd.server import members, server_info

router = APIRouter(prefix="/api")


@router.get("/server")
async def server(_: SessionDep, lxd: LXDDep) -> dict[str, Any]:
    info = await server_info(lxd)
    return {**info.as_dict(), "endpoint": lxd.endpoint}


@router.get("/cluster/members")
async def cluster_members(_: ClusterReadDep, lxd: LXDDep) -> list[dict[str, Any]]:
    return [m.as_dict() for m in await members(lxd)]


@router.get("/metrics/live")
async def live_metrics(scope: ScopeDep, live: LiveDep) -> dict[str, Any]:
    """A project user gets their instances only, and no member rows (cluster-wide)."""
    snap = await live.snapshot()
    if scope.everything:
        return snap
    return {**snap, "members": [], "instances": scope.keep(snap["instances"])}


@router.get("/cluster/members/{name}/resources")
async def member_resources(name: str, _: ClusterReadDep, lxd: LXDDep) -> dict[str, Any]:
    """Hardware inventory of one member (CL-2)."""
    info = await server_info(lxd)
    if name not in {m.name for m in await members(lxd, info)}:
        raise HTTPException(status_code=404, detail="no such member")
    # a standalone server refuses a target ("Cluster member not found"; D91 review)
    data: dict[str, Any] = await lxd.get(
        "/1.0/resources", **({"target": name} if info.clustered else {})
    )
    return data


@router.get("/storage-pools")
async def storage_pools(session: SessionDep, lxd: LXDDep) -> list[dict[str, Any]]:
    """Every signed-in user needs pool names for the create wizard; a pool's config (its
    source devices, Ceph user and cluster names) only a global role sees (D89)."""
    data = await lxd.get("/1.0/storage-pools", recursion=1)
    everything = session.user.role != "project"
    return [
        {
            "name": p["name"],
            "driver": p.get("driver", ""),
            "status": p.get("status", ""),
            "description": p.get("description", ""),
            "config": (p.get("config") or {}) if everything else {},
            "used_by": len(p.get("used_by") or []),
            "locations": p.get("locations") or [],
        }
        for p in data or []
    ]
