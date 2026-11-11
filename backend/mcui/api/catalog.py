"""Lists the create-instance wizard needs: local and remote images, profiles, networks (IN-2).

Phase 3f extends images and profiles with management.
"""

from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException

from mcui.api.deps import LXDDep, ServicesDep, SessionDep
from mcui.lxd.remotes import ARCH, REMOTES, RemoteCatalog

router = APIRouter(prefix="/api")


async def get_catalog(services: ServicesDep) -> RemoteCatalog:
    if services.remotes is None:  # pragma: no cover - set together with LXD, which is checked first
        raise HTTPException(status_code=503, detail="LXD is not configured")
    return services.remotes


CatalogDep = Annotated[RemoteCatalog, Depends(get_catalog)]


@router.get("/images")
async def local_images(
    _: SessionDep, lxd: LXDDep, project: str = "default"
) -> list[dict[str, Any]]:
    data = await lxd.get("/1.0/images", recursion=1, project=project)
    return [
        {
            "fingerprint": i["fingerprint"],
            "aliases": [a["name"] for a in i.get("aliases") or []],
            "description": (i.get("properties") or {}).get("description", ""),
            "type": i.get("type", ""),
            "architecture": i.get("architecture", ""),
            "size": i.get("size", 0),
            "created_at": i.get("created_at", ""),
            "uploaded_at": i.get("uploaded_at", ""),
            "last_used_at": i.get("last_used_at", ""),
            "cached": i.get("cached", False),
            "public": i.get("public", False),
            "auto_update": i.get("auto_update", False),
            "properties": i.get("properties") or {},
            "project": project,
        }
        for i in data or []
    ]


@router.get("/images/remote/{remote}")
async def remote_images(
    remote: str, _: SessionDep, lxd: LXDDep, catalog: CatalogDep, arch: str | None = None
) -> list[dict[str, Any]]:
    """Images on ``images:`` or ``ubuntu:``, for the cluster's architectures by default."""
    if remote not in REMOTES:
        raise HTTPException(status_code=404, detail=f"unknown remote; use one of {sorted(REMOTES)}")
    if arch is None:
        env = (await lxd.get("/1.0")).get("environment") or {}
        arches = {ARCH.get(a, a) for a in env.get("architectures") or []}
    else:
        arches = {arch}
    try:
        items = await catalog.images(remote)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"{remote}: {type(e).__name__}: {e}") from e
    return [i.as_dict() for i in items if not arches or i.arch in arches]


@router.get("/profiles")
async def profiles(_: SessionDep, lxd: LXDDep, project: str = "default") -> list[dict[str, Any]]:
    data = await lxd.get("/1.0/profiles", recursion=1, project=project)
    return [
        {
            "name": p["name"],
            "description": p.get("description", ""),
            "config": p.get("config") or {},
            "devices": p.get("devices") or {},
            "used_by": p.get("used_by") or [],
            "project": project,
        }
        for p in data or []
    ]


@router.get("/networks")
async def networks(
    _: SessionDep, lxd: LXDDep, project: str = "default", target: str | None = None
) -> list[dict[str, Any]]:
    """Networks of a project; ``target`` lists that member's own interfaces too."""
    params: dict[str, Any] = {"project": project}
    if target:
        params["target"] = target
    data = await lxd.get("/1.0/networks", recursion=1, **params)
    return [
        {
            "name": n["name"],
            "type": n.get("type", ""),
            "managed": n.get("managed", False),
            "description": n.get("description", ""),
            "status": n.get("status", ""),
            "config": n.get("config") or {},
            "used_by": len(n.get("used_by") or []),
            "locations": n.get("locations") or [],
        }
        for n in data or []
    ]
