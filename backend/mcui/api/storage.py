"""Storage pools: detail, per-member settings and capacity, changes (ST-1, ST-2).

Local drivers (dir, zfs, btrfs, lvm) keep a pool per member, so in a cluster
they are created in two steps (``mcui.lxd.clustered``) with each member's own
``source``/``size``/pool names; Ceph and CephFS pools are created once (D54).
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from mcui.api.audited import audited
from mcui.api.deps import AdminDep, LXDDep, SessionDep, StoreDep
from mcui.api.instances import NAME
from mcui.api.manage import ObjName
from mcui.api.networks import MemberName
from mcui.lxd import clustered, operations
from mcui.lxd.client import LXDError
from mcui.lxd.server import members, server_info

router = APIRouter(prefix="/api")

Driver = Literal["dir", "zfs", "btrfs", "lvm", "ceph", "cephfs"]
LOCAL_DRIVERS = frozenset({"dir", "zfs", "btrfs", "lvm"})
# keys LXD keeps per member
MEMBER_KEYS = frozenset({"source", "size", "zfs.pool_name", "lvm.vg_name", "lvm.thinpool_name"})


def _pool(name: str) -> str:
    return f"/1.0/storage-pools/{name}"


def _target(target: str | None) -> dict[str, Any]:
    return {"target": target} if target else {}


@router.get("/storage-pools/{name}")
async def get_pool(
    name: ObjName, _: SessionDep, lxd: LXDDep, target: MemberName = None
) -> dict[str, Any]:
    data: dict[str, Any] = await lxd.get(_pool(name), **_target(target))
    return data


@router.get("/storage-pools/{name}/members")
async def pool_members(name: ObjName, _: SessionDep, lxd: LXDDep) -> list[dict[str, Any]]:
    """Each member's own settings and capacity (one entry when not clustered)."""
    info = await server_info(lxd)

    async def one(member: str | None) -> dict[str, Any]:
        out: dict[str, Any] = {"member": member or info.server_name}
        try:
            p, res = await asyncio.gather(
                lxd.get(_pool(name), **_target(member)),
                lxd.get(f"{_pool(name)}/resources", **_target(member)),
            )
        except LXDError as e:
            return {**out, "config": {}, "error": str(e)}
        cfg = p.get("config") or {}
        return {
            **out,
            "config": {k: v for k, v in cfg.items() if k in MEMBER_KEYS},
            "status": p.get("status", ""),
            "resources": res,
        }

    if not info.clustered:
        return [await one(None)]
    return list(await asyncio.gather(*(one(m.name) for m in await members(lxd, info))))


class PoolBody(BaseModel):
    description: str = ""
    config: dict[str, str] = Field(default_factory=dict)


class NewPool(PoolBody):
    name: str = Field(pattern=NAME, max_length=63)
    driver: Driver
    members: dict[str, dict[str, str]] = Field(default_factory=dict)


def _check_member_keys(members_cfg: dict[str, dict[str, str]]) -> None:
    for member, cfg in members_cfg.items():
        bad = sorted(set(cfg) - MEMBER_KEYS)
        if bad:
            raise HTTPException(422, f"{member}: not a per-member key: {', '.join(bad)}")


@router.post("/storage-pools")
async def create_pool(
    body: NewPool, session: AdminDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    _check_member_keys(body.members)
    if body.driver not in LOCAL_DRIVERS and body.members:
        raise HTTPException(422, f"{body.driver} pools have no per-member settings")
    st = await audited(
        session,
        store,
        "storage-pool.create",
        body.name,
        clustered.create(
            lxd,
            "/1.0/storage-pools",
            {"name": body.name, "driver": body.driver},
            body.description,
            body.config,
            body.members,
            per_member=body.driver in LOCAL_DRIVERS,
        ),
        detail={"driver": body.driver, **({"members": body.members} if body.members else {})},
    )
    return st.as_dict()


@router.put("/storage-pools/{name}")
async def update_pool(
    name: ObjName,
    body: PoolBody,
    session: AdminDep,
    store: StoreDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> dict[str, Any]:
    """Replace the cluster-wide settings, or with ``target`` one member's own keys."""
    payload = body.model_dump()
    if target:
        _check_member_keys({target: body.config})
        # like networks (D52), keep the cluster-wide description
        current = await lxd.get(_pool(name))
        payload["description"] = current.get("description", "")
    st = await audited(
        session,
        store,
        "storage-pool.update",
        name,
        operations.start(lxd, "PUT", _pool(name), json=payload, params=_target(target) or None),
        detail={"target": target} if target else None,
    )
    return st.as_dict()


@router.delete("/storage-pools/{name}")
async def delete_pool(
    name: ObjName, session: AdminDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    st = await audited(
        session, store, "storage-pool.delete", name, operations.start(lxd, "DELETE", _pool(name))
    )
    return st.as_dict()
