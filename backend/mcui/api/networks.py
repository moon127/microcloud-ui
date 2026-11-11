"""Networks: detail, per-member state and config, leases, allocations, changes (NW-1, NW-2).

The list lives in ``catalog`` (it also feeds the create wizard). In a cluster,
bridge, physical and macvlan networks are created in two steps: a pending
definition on every member (carrying the member-specific keys such as
``parent``), then the cluster-wide definition. OVN networks need only the
second step. Both steps are audited as one change (D52).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from mcui.api.audited import audited
from mcui.api.deps import AdminDep, LXDDep, SessionDep, StoreDep
from mcui.api.instances import NAME, ProjectName
from mcui.api.manage import ObjName
from mcui.lxd import operations
from mcui.lxd.client import LXDClient, LXDError
from mcui.lxd.server import members, server_info

router = APIRouter(prefix="/api")

NetType = Literal["bridge", "ovn", "physical", "macvlan"]
# keys LXD keeps per member rather than cluster-wide
MEMBER_KEYS = frozenset(
    {"parent", "bridge.external_interfaces", "bgp.ipv4.nexthop", "bgp.ipv6.nexthop"}
)
MemberName = Annotated[str | None, Query(pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]*$", max_length=253)]


def _net(name: str) -> str:
    return f"/1.0/networks/{name}"


@router.get("/networks/{project}/{name}")
async def get_network(
    project: ProjectName, name: ObjName, _: SessionDep, lxd: LXDDep, target: MemberName = None
) -> dict[str, Any]:
    params: dict[str, Any] = {"project": project}
    if target:
        params["target"] = target
    data: dict[str, Any] = await lxd.get(_net(name), **params)
    return data


@router.get("/networks/{project}/{name}/state")
async def network_state(
    project: ProjectName, name: ObjName, _: SessionDep, lxd: LXDDep, target: MemberName = None
) -> dict[str, Any]:
    params: dict[str, Any] = {"project": project}
    if target:
        params["target"] = target
    data: dict[str, Any] = await lxd.get(f"{_net(name)}/state", **params)
    return data


@router.get("/networks/{project}/{name}/members")
async def network_members(
    project: ProjectName, name: ObjName, _: SessionDep, lxd: LXDDep
) -> list[dict[str, Any]]:
    """Each member's own settings for the network (``parent`` and the like)."""
    info = await server_info(lxd)
    if not info.clustered:
        return []

    async def one(member: str) -> dict[str, Any]:
        try:
            n = await lxd.get(_net(name), project=project, target=member)
        except LXDError as e:
            return {"member": member, "config": {}, "error": str(e)}
        cfg = {k: v for k, v in (n.get("config") or {}).items() if k in MEMBER_KEYS}
        return {"member": member, "config": cfg}

    return list(await asyncio.gather(*(one(m.name) for m in await members(lxd, info))))


@router.get("/networks/{project}/{name}/leases")
async def network_leases(
    project: ProjectName, name: ObjName, _: SessionDep, lxd: LXDDep
) -> list[dict[str, Any]]:
    data = await lxd.get(f"{_net(name)}/leases", project=project)
    return list(data or [])


@router.get("/network-allocations")
async def network_allocations(
    _: SessionDep, lxd: LXDDep, project: str | None = None
) -> list[dict[str, Any]]:
    params = {"project": project} if project else {"all-projects": "true"}
    data = await lxd.get("/1.0/network-allocations", **params)
    return list(data or [])


class NetworkBody(BaseModel):
    description: str = ""
    config: dict[str, str] = Field(default_factory=dict)


class NewNetwork(NetworkBody):
    name: str = Field(pattern=NAME, max_length=15)  # it becomes a Linux interface name
    type: NetType
    project: str = Field(default="default", pattern=NAME, max_length=63)
    # member name -> that member's own keys (only used in a cluster)
    members: dict[str, dict[str, str]] = Field(default_factory=dict)


def _check_member_keys(members_cfg: dict[str, dict[str, str]]) -> None:
    for member, cfg in members_cfg.items():
        bad = sorted(set(cfg) - MEMBER_KEYS)
        if bad:
            raise HTTPException(422, f"{member}: not a per-member key: {', '.join(bad)}")


async def _create(lxd: LXDClient, body: NewNetwork) -> operations.Started:
    info = await server_info(lxd)
    params = {"project": body.project}
    pending = info.clustered and body.type != "ovn"
    if pending:
        try:
            for m in await members(lxd, info):
                await operations.start(
                    lxd,
                    "POST",
                    "/1.0/networks",
                    json={
                        "name": body.name,
                        "type": body.type,
                        "config": body.members.get(m.name, {}),
                    },
                    params={**params, "target": m.name},
                )
        except LXDError:
            await _forget_pending(lxd, body.name, params)
            raise
    config = dict(body.config)
    if not info.clustered:
        # a standalone server has no per-member step: its own keys go in the config
        for cfg in body.members.values():
            config.update(cfg)
    try:
        return await operations.start(
            lxd,
            "POST",
            "/1.0/networks",
            json={
                "name": body.name,
                "type": body.type,
                "description": body.description,
                "config": config,
            },
            params=params,
        )
    except LXDError:
        if pending:
            await _forget_pending(lxd, body.name, params)
        raise


async def _forget_pending(lxd: LXDClient, name: str, params: dict[str, Any]) -> None:
    """Remove a half-made network so the name can be used again."""
    # nothing was pending, or it's gone already
    with contextlib.suppress(LXDError):
        await operations.start(lxd, "DELETE", _net(name), params=params)


@router.post("/networks")
async def create_network(
    body: NewNetwork, session: AdminDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    _check_member_keys(body.members)
    st = await audited(
        session,
        store,
        "network.create",
        f"{body.project}/{body.name}",
        _create(lxd, body),
        detail={"type": body.type, "members": body.members}
        if body.members
        else {"type": body.type},
    )
    return st.as_dict()


@router.put("/networks/{project}/{name}")
async def update_network(
    project: ProjectName,
    name: ObjName,
    body: NetworkBody,
    session: AdminDep,
    store: StoreDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> dict[str, Any]:
    """Replace the cluster-wide settings, or with ``target`` one member's own keys."""
    params: dict[str, Any] = {"project": project}
    payload = body.model_dump()
    if target:
        _check_member_keys({target: body.config})
        params["target"] = target
        # LXD applies a member-level PUT's description cluster-wide (seen live:
        # it blanked it), so keep the current one
        current = await lxd.get(_net(name), project=project)
        payload["description"] = current.get("description", "")
    st = await audited(
        session,
        store,
        "network.update",
        f"{project}/{name}",
        operations.start(lxd, "PUT", _net(name), json=payload, params=params),
        detail={"target": target} if target else None,
    )
    return st.as_dict()


@router.delete("/networks/{project}/{name}")
async def delete_network(
    project: ProjectName, name: ObjName, session: AdminDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    st = await audited(
        session,
        store,
        "network.delete",
        f"{project}/{name}",
        operations.start(lxd, "DELETE", _net(name), params={"project": project}),
    )
    return st.as_dict()
