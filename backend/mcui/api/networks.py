"""Networks: detail, per-member state and config, leases, allocations, changes (NW-1, NW-2).

The list lives in ``catalog`` (it also feeds the create wizard). In a cluster,
bridge, physical and macvlan networks are created in two steps
(``mcui.lxd.clustered``); OVN networks need only the second. The steps are
audited as one change (D52).
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from mcui.api.audited import audited
from mcui.api.deps import InBodyDep, LXDDep, ProjectManageDep, ProjectReadDep, ScopeDep, StoreDep
from mcui.api.instances import NAME, ProjectName
from mcui.api.manage import ObjName
from mcui.lxd import clustered, operations
from mcui.lxd.client import LXDClient, LXDError
from mcui.lxd.server import members, server_info
from mcui.lxd.volatile import keep_volatile

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
    project: ProjectName, name: ObjName, _: ProjectReadDep, lxd: LXDDep, target: MemberName = None
) -> dict[str, Any]:
    params: dict[str, Any] = {"project": project}
    if target:
        params["target"] = target
    data: dict[str, Any] = await lxd.get(_net(name), **params)
    return data


@router.get("/networks/{project}/{name}/state")
async def network_state(
    project: ProjectName, name: ObjName, _: ProjectReadDep, lxd: LXDDep, target: MemberName = None
) -> dict[str, Any]:
    params: dict[str, Any] = {"project": project}
    if target:
        params["target"] = target
    data: dict[str, Any] = await lxd.get(f"{_net(name)}/state", **params)
    return data


@router.get("/networks/{project}/{name}/members")
async def network_members(
    project: ProjectName, name: ObjName, _: ProjectReadDep, lxd: LXDDep
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
    project: ProjectName, name: ObjName, _: ProjectReadDep, lxd: LXDDep
) -> list[dict[str, Any]]:
    data = await lxd.get(f"{_net(name)}/leases", project=project)
    return list(data or [])


@router.get("/network-allocations")
async def network_allocations(
    _: ScopeDep, lxd: LXDDep, project: str | None = None
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
    return await clustered.create(
        lxd,
        "/1.0/networks",
        {"name": body.name, "type": body.type},
        body.description,
        body.config,
        body.members,
        per_member=body.type != "ovn",
        params={"project": body.project},
    )


@router.post("/networks")
async def create_network(
    body: NewNetwork, guard: InBodyDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    await guard.check(body.project)
    session = guard.session
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
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> dict[str, Any]:
    """Replace the cluster-wide settings, or with ``target`` one member's own keys."""
    params: dict[str, Any] = {"project": project}
    payload = body.model_dump()
    current = await lxd.get(_net(name), project=project)
    if target:
        _check_member_keys({target: body.config})
        params["target"] = target
        # LXD applies a member-level PUT's description cluster-wide (seen live:
        # it blanked it), so keep the current one
        payload["description"] = current.get("description", "")
    else:
        payload["config"] = keep_volatile(current.get("config"), body.config)
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
    project: ProjectName, name: ObjName, session: ProjectManageDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    st = await audited(
        session,
        store,
        "network.delete",
        f"{project}/{name}",
        operations.start(lxd, "DELETE", _net(name), params={"project": project}),
    )
    return st.as_dict()
