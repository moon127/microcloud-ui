"""Custom storage volumes: CRUD, resize, snapshots and schedules, attach/detach (ST-3).

Volumes belong to a project and a pool; on local pools in a cluster they also
live on one member, so changes pass that member back as ``target`` (D55).
Config saves keep LXD's ``volatile.*`` keys (``mcui.lxd.volatile``).
Attaching adds a disk device with PATCH (LXD merges devices); detaching
rewrites the instance without the device, guarded by LXD's ETag.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field

from mcui.api.audited import audited
from mcui.api.deps import (
    InBodyDep,
    LXDDep,
    ProjectManageDep,
    ProjectOperateDep,
    ProjectReadDep,
    ScopeDep,
    StoreDep,
)
from mcui.api.instances import NAME, ProjectName
from mcui.api.manage import ObjName, Rename
from mcui.api.networks import MemberName
from mcui.auth.store import Session, Store
from mcui.lxd import operations
from mcui.lxd.client import LXDClient, LXDError
from mcui.lxd.volatile import keep_volatile

router = APIRouter(prefix="/api/volumes")

SnapName = Annotated[str, Path(pattern=NAME, max_length=63)]
DEVICE = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
WRITABLE = ("architecture", "config", "devices", "ephemeral", "profiles", "stateful", "description")


def _vol(pool: str, name: str | None = None) -> str:
    base = f"/1.0/storage-pools/{pool}/volumes/custom"
    return f"{base}/{name}" if name else base


def _params(project: str, target: str | None) -> dict[str, Any]:
    return {"project": project, **({"target": target} if target else {})}


async def _change(
    session: Session,
    store: Store,
    lxd: LXDClient,
    action: str,
    target: str,
    method: str,
    path: str,
    params: dict[str, Any],
    json: Any = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    st = await audited(
        session,
        store,
        action,
        target,
        operations.start(lxd, method, path, json=json, params=params),
        detail=detail,
    )
    return st.as_dict()


# --- reading ---------------------------------------------------------------------


@router.get("")
async def list_volumes(
    scope: ScopeDep,
    lxd: LXDDep,
    project: str = "default",
    pool: str | None = None,
    all_types: Annotated[bool, Query(alias="all")] = False,
) -> list[dict[str, Any]]:
    """Custom volumes of a project on every pool (or one); ``all`` adds instance/image volumes."""
    scope.require(project)
    pools = (
        [pool] if pool else [p["name"] for p in await lxd.get("/1.0/storage-pools", recursion=1)]
    )

    async def one(p: str) -> list[dict[str, Any]]:
        try:
            vols = await lxd.get(f"/1.0/storage-pools/{p}/volumes", recursion=1, project=project)
        except LXDError:
            return []  # a pool that's unavailable on the answering member
        # LXD lists snapshots as "vol/snap" too; they belong on the volume's page
        return [
            {**v, "pool": p}
            for v in vols or []
            if "/" not in v.get("name", "") and (all_types or v.get("type") == "custom")
        ]

    return [v for vs in await asyncio.gather(*(one(p) for p in pools)) for v in vs]


@router.get("/{project}/{pool}/{name}")
async def get_volume(
    project: ProjectName,
    pool: ObjName,
    name: ObjName,
    _: ProjectReadDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> dict[str, Any]:
    params = _params(project, target)
    vol, state = await asyncio.gather(
        lxd.get(_vol(pool, name), **params),
        _state(lxd, f"{_vol(pool, name)}/state", params),
    )
    return {**vol, "pool": pool, "state": state}


async def _state(lxd: LXDClient, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
    try:
        data: dict[str, Any] = await lxd.get(path, **params)
    except LXDError:
        return None  # some drivers can't report usage
    return data


@router.get("/{project}/{pool}/{name}/snapshots")
async def list_snapshots(
    project: ProjectName,
    pool: ObjName,
    name: ObjName,
    _: ProjectReadDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> list[dict[str, Any]]:
    data = await lxd.get(f"{_vol(pool, name)}/snapshots", recursion=1, **_params(project, target))
    return list(data or [])


# --- changes ---------------------------------------------------------------------


class VolumeBody(BaseModel):
    description: str = ""
    config: dict[str, str] = Field(default_factory=dict)


class NewVolume(VolumeBody):
    name: str = Field(pattern=NAME, max_length=63)
    pool: str = Field(pattern=NAME, max_length=63)
    project: str = Field(default="default", pattern=NAME, max_length=63)
    content_type: Literal["filesystem", "block"] = "filesystem"
    target: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]*$")


@router.post("")
async def create_volume(
    body: NewVolume, guard: InBodyDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    await guard.check(body.project)
    session = guard.session
    return await _change(
        session,
        store,
        lxd,
        "volume.create",
        f"{body.project}/{body.pool}/{body.name}",
        "POST",
        _vol(body.pool),
        _params(body.project, body.target),
        json={
            "name": body.name,
            "type": "custom",
            "content_type": body.content_type,
            "description": body.description,
            "config": body.config,
        },
        detail={
            "content_type": body.content_type,
            **({"size": body.config["size"]} if "size" in body.config else {}),
        },
    )


@router.put("/{project}/{pool}/{name}")
async def update_volume(
    project: ProjectName,
    pool: ObjName,
    name: ObjName,
    body: VolumeBody,
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> dict[str, Any]:
    """Description and config as a whole; a bigger ``size`` resizes it."""
    params = _params(project, target)
    current = await lxd.get(_vol(pool, name), **params)
    return await _change(
        session,
        store,
        lxd,
        "volume.update",
        f"{project}/{pool}/{name}",
        "PUT",
        _vol(pool, name),
        params,
        json={
            "description": body.description,
            "config": keep_volatile(current.get("config"), body.config),
        },
    )


@router.post("/{project}/{pool}/{name}/rename")
async def rename_volume(
    project: ProjectName,
    pool: ObjName,
    name: ObjName,
    body: Rename,
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "volume.rename",
        f"{project}/{pool}/{name}",
        "POST",
        _vol(pool, name),
        _params(project, target),
        json={"name": body.name},
        detail={"to": body.name},
    )


@router.delete("/{project}/{pool}/{name}")
async def delete_volume(
    project: ProjectName,
    pool: ObjName,
    name: ObjName,
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "volume.delete",
        f"{project}/{pool}/{name}",
        "DELETE",
        _vol(pool, name),
        _params(project, target),
    )


# --- snapshots ---------------------------------------------------------------------


class NewSnapshot(BaseModel):
    name: str = Field(pattern=NAME, max_length=63)
    expires_at: str | None = None


@router.post("/{project}/{pool}/{name}/snapshots")
async def create_snapshot(
    project: ProjectName,
    pool: ObjName,
    name: ObjName,
    body: NewSnapshot,
    session: ProjectOperateDep,
    store: StoreDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "volume.snapshot.create",
        f"{project}/{pool}/{name}/{body.name}",
        "POST",
        f"{_vol(pool, name)}/snapshots",
        _params(project, target),
        json=body.model_dump(exclude_none=True),
    )


@router.post("/{project}/{pool}/{name}/snapshots/{snap}/rename")
async def rename_snapshot(
    project: ProjectName,
    pool: ObjName,
    name: ObjName,
    snap: SnapName,
    body: Rename,
    session: ProjectOperateDep,
    store: StoreDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "volume.snapshot.rename",
        f"{project}/{pool}/{name}/{snap}",
        "POST",
        f"{_vol(pool, name)}/snapshots/{snap}",
        _params(project, target),
        json={"name": body.name},
        detail={"to": body.name},
    )


@router.post("/{project}/{pool}/{name}/snapshots/{snap}/restore")
async def restore_snapshot(
    project: ProjectName,
    pool: ObjName,
    name: ObjName,
    snap: SnapName,
    session: ProjectOperateDep,
    store: StoreDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> dict[str, Any]:
    params = _params(project, target)
    current = await lxd.get(_vol(pool, name), **params)
    return await _change(
        session,
        store,
        lxd,
        "volume.snapshot.restore",
        f"{project}/{pool}/{name}/{snap}",
        "PUT",
        _vol(pool, name),
        params,
        json={
            "description": current.get("description", ""),
            "config": current.get("config") or {},
            "restore": snap,
        },
    )


@router.delete("/{project}/{pool}/{name}/snapshots/{snap}")
async def delete_snapshot(
    project: ProjectName,
    pool: ObjName,
    name: ObjName,
    snap: SnapName,
    session: ProjectOperateDep,
    store: StoreDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "volume.snapshot.delete",
        f"{project}/{pool}/{name}/{snap}",
        "DELETE",
        f"{_vol(pool, name)}/snapshots/{snap}",
        _params(project, target),
    )


# --- attach / detach -----------------------------------------------------------------


class Attach(BaseModel):
    instance: str = Field(pattern=NAME, max_length=63)
    device: str = Field(pattern=DEVICE, max_length=63)
    path: str | None = Field(default=None, pattern=r"^/", max_length=4096)
    # an ISO: boot from it before the root disk (LXD boots the highest first)
    boot_priority: int | None = Field(default=None, ge=0, le=100)


class Detach(BaseModel):
    instance: str = Field(pattern=NAME, max_length=63)
    device: str = Field(pattern=DEVICE, max_length=63)


@router.post("/{project}/{pool}/{name}/attach")
async def attach(
    project: ProjectName,
    pool: ObjName,
    name: ObjName,
    body: Attach,
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    """Filesystem volumes need a mount ``path``; block and ISO volumes are attached without
    one, an ISO perhaps with a ``boot_priority``."""
    dev = {
        "type": "disk",
        "pool": pool,
        "source": name,
        **({"path": body.path} if body.path else {}),
        **({"boot.priority": str(body.boot_priority)} if body.boot_priority is not None else {}),
    }
    return await _change(
        session,
        store,
        lxd,
        "volume.attach",
        f"{project}/{pool}/{name}",
        "PATCH",
        f"/1.0/instances/{body.instance}",
        {"project": project},
        json={"devices": {body.device: dev}},
        detail={"instance": body.instance, "device": body.device},
    )


async def _detach(
    lxd: LXDClient, project: str, pool: str, name: str, body: Detach
) -> operations.Started:
    path = f"/1.0/instances/{body.instance}"
    r = await lxd.raw("GET", path, params={"project": project})
    if r.status_code != 200:
        raise LXDError(r.json().get("error") or f"HTTP {r.status_code}", r.status_code)
    inst = r.json()["metadata"]
    devices = dict(inst.get("devices") or {})
    dev = devices.get(body.device)
    if not dev or dev.get("type") != "disk" or dev.get("pool") != pool or dev.get("source") != name:
        raise HTTPException(
            409, f"{body.device} on {body.instance} is not this volume (it may come from a profile)"
        )
    del devices[body.device]
    put = {k: inst[k] for k in WRITABLE if k in inst} | {"devices": devices}
    etag = r.headers.get("etag")
    resp = await lxd.raw(
        "PUT",
        path,
        params={"project": project},
        json=put,
        headers={"If-Match": etag} if etag else None,
    )
    data = resp.json()
    if resp.status_code >= 400 or data.get("type") == "error":
        raise LXDError(str(data.get("error") or f"HTTP {resp.status_code}"), resp.status_code)
    if data.get("type") == "async":
        return operations.Started(operation=data.get("metadata"))
    return operations.Started(operation=None, metadata=data.get("metadata"))


@router.post("/{project}/{pool}/{name}/detach")
async def detach(
    project: ProjectName,
    pool: ObjName,
    name: ObjName,
    body: Detach,
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    st = await audited(
        session,
        store,
        "volume.detach",
        f"{project}/{pool}/{name}",
        _detach(lxd, project, pool, name, body),
        detail={"instance": body.instance, "device": body.device},
    )
    return st.as_dict()
