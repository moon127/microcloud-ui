"""OVN network peers (NW-7, Phase 10a): list, create, edit, delete.

A peer links two OVN networks on the same uplink so they route to each other directly.
It shows ``Pending`` until the other network peers back, then ``Created`` (seen live on
LXD 6.10). The other side is a separate peer in the other network's project, so making
both is two requests, each needing manage in its own project (D84).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from mcui.api.acls import _change
from mcui.api.deps import InBodyDep, LXDDep, ProjectManageDep, ProjectReadDep, StoreDep
from mcui.api.instances import NAME, ProjectName
from mcui.api.manage import ObjName

router = APIRouter(prefix="/api/networks")


def _peers(network: str, name: str | None = None) -> str:
    base = f"/1.0/networks/{network}/peers"
    return f"{base}/{name}" if name else base


class PeerBody(BaseModel):
    description: str = ""
    config: dict[str, str] = Field(default_factory=dict)


class NewPeer(PeerBody):
    name: str = Field(pattern=NAME, max_length=63)
    target_project: str = Field(pattern=NAME, max_length=63)
    target_network: str = Field(pattern=NAME, max_length=63)


@router.get("/{project}/{network}/peers")
async def list_peers(
    project: ProjectName, network: ObjName, _: ProjectReadDep, lxd: LXDDep
) -> list[dict[str, Any]]:
    return list(await lxd.get(_peers(network), recursion=1, project=project) or [])


@router.post("/{project}/{network}/peers")
async def create_peer(
    project: ProjectName,
    network: ObjName,
    body: NewPeer,
    guard: InBodyDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    """Manage in this project; read in the target's, since naming its network shows
    whether it exists."""
    await guard.check(project)
    await guard.check(body.target_project, "read")
    return await _change(
        guard.session,
        store,
        lxd,
        "network-peer.create",
        f"{project}/{network}/{body.name}",
        "POST",
        _peers(network),
        {"project": project},
        json=body.model_dump(),
        detail={"to": f"{body.target_project}/{body.target_network}"},
    )


@router.put("/{project}/{network}/peers/{name}")
async def update_peer(
    project: ProjectName,
    network: ObjName,
    name: ObjName,
    body: PeerBody,
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "network-peer.update",
        f"{project}/{network}/{name}",
        "PUT",
        _peers(network, name),
        {"project": project},
        json=body.model_dump(),
    )


@router.delete("/{project}/{network}/peers/{name}")
async def delete_peer(
    project: ProjectName,
    network: ObjName,
    name: ObjName,
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "network-peer.delete",
        f"{project}/{network}/{name}",
        "DELETE",
        _peers(network, name),
        {"project": project},
    )
