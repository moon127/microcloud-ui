"""Placement groups (LXD 6.6+, ``instance_placement_groups``; Phase 5b).

A placement group spreads or packs instances across cluster members: an instance joins
one through its ``placement.group`` key. ``policy`` (spread or compact) is required;
``rigor`` decides whether the policy may be bent (permissive) or not (strict). LXD
refuses to delete a group that instances still use. Every change: admin + CSRF, audited.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from mcui.api.acls import _change
from mcui.api.deps import InBodyDep, LXDDep, ProjectManageDep, ProjectReadDep, ScopeDep, StoreDep
from mcui.api.instances import NAME, ProjectName
from mcui.api.manage import ObjName, Rename

router = APIRouter(prefix="/api/placement-groups")


class GroupBody(BaseModel):
    description: str = ""
    policy: Literal["spread", "compact"]
    rigor: Literal["strict", "permissive"] = "strict"

    def lxd(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "config": {"policy": self.policy, "rigor": self.rigor},
        }


class NewGroup(GroupBody):
    name: str = Field(pattern=NAME, max_length=63)
    project: str = Field(default="default", pattern=NAME, max_length=63)


def _group(name: str) -> str:
    return f"/1.0/placement-groups/{name}"


@router.get("")
async def list_groups(_: ScopeDep, lxd: LXDDep, project: str = "default") -> list[dict[str, Any]]:
    data = await lxd.get("/1.0/placement-groups", recursion=1, project=project)
    return list(data or [])


@router.get("/{project}/{name}")
async def get_group(
    project: ProjectName, name: ObjName, _: ProjectReadDep, lxd: LXDDep
) -> dict[str, Any]:
    data: dict[str, Any] = await lxd.get(_group(name), project=project)
    return data


@router.post("")
async def create_group(
    body: NewGroup, guard: InBodyDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    await guard.check(body.project)
    session = guard.session
    return await _change(
        session,
        store,
        lxd,
        "placement-group.create",
        f"{body.project}/{body.name}",
        "POST",
        "/1.0/placement-groups",
        {"project": body.project},
        json={"name": body.name, **body.lxd()},
    )


@router.put("/{project}/{name}")
async def update_group(
    project: ProjectName,
    name: ObjName,
    body: GroupBody,
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "placement-group.update",
        f"{project}/{name}",
        "PUT",
        _group(name),
        {"project": project},
        json=body.lxd(),
    )


@router.post("/{project}/{name}/rename")
async def rename_group(
    project: ProjectName,
    name: ObjName,
    body: Rename,
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "placement-group.rename",
        f"{project}/{name}",
        "POST",
        _group(name),
        {"project": project},
        json={"name": body.name},
        detail={"to": body.name},
    )


@router.delete("/{project}/{name}")
async def delete_group(
    project: ProjectName, name: ObjName, session: ProjectManageDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "placement-group.delete",
        f"{project}/{name}",
        "DELETE",
        _group(name),
        {"project": project},
    )
