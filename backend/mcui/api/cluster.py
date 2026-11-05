"""Read-only cluster views and live metrics (CF-3, CL-1 basics, MO-1)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

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
