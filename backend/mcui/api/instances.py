"""Instances: list, detail, lifecycle, config, rename/copy/move, snapshots, logs, publish.

IN-1, IN-3, IN-4, IN-5, IN-6, IN-10, IN-12. Every change needs the CSRF token and the
right in the instance's project (operate: lifecycle and snapshots; manage: the rest, D81),
and is audited with its LXD operation (D42).
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, Field

from mcui.api.audited import audited
from mcui.api.deps import (
    InBody,
    InBodyDep,
    LXDDep,
    ProjectManageDep,
    ProjectOperateDep,
    ProjectReadDep,
    ScopeDep,
    ServicesDep,
    StoreDep,
)
from mcui.auth.store import Session, Store
from mcui.lxd import operations
from mcui.lxd.client import LXDClient
from mcui.lxd.instances import InstanceIndex
from mcui.lxd.remotes import REMOTES

router = APIRouter(prefix="/api/instances")

NAME = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"  # LXD instance, snapshot and project names
# validated in the path too, so "..", "%2F" etc. never reach LXD's URL space
ProjectName = Annotated[str, Path(pattern=NAME, max_length=63)]
InstName = Annotated[str, Path(pattern=NAME, max_length=63)]
SnapName = Annotated[str, Path(pattern=NAME, max_length=63)]
LogName = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$", max_length=255)]


async def get_index(services: ServicesDep) -> InstanceIndex:
    if services.instances is None:
        raise HTTPException(status_code=503, detail="LXD is not configured")
    return services.instances


IndexDep = Annotated[InstanceIndex, Depends(get_index)]


class Ctx:
    """Everything a change needs: who, where, and how to audit it."""

    def __init__(
        self,
        session: Session,
        store: Store,
        lxd: LXDClient,
        index: InstanceIndex,
        in_body: InBody | None = None,
    ) -> None:
        self.session, self.store, self.lxd, self.index = session, store, lxd, index
        self.in_body = in_body  # creates: check the project named in the body

    async def check_project(self, project: str) -> None:
        if self.in_body is None:  # pragma: no cover - a route wiring error
            raise RuntimeError("check_project needs CtxCreateDep or CtxManageDep")
        await self.in_body.check(project)

    async def audit(
        self,
        action: str,
        project: str,
        name: str,
        call: Awaitable[operations.Started],
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Audit an arbitrary sequence of LXD calls as one change."""
        st = await audited(
            self.session, self.store, action, f"{project}/{name}", call, detail=detail
        )
        self.index.invalidate()
        return st.as_dict()

    async def change(
        self,
        action: str,
        project: str,
        name: str,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        st = await audited(
            self.session,
            self.store,
            action,
            f"{project}/{name}",
            operations.start(
                self.lxd, method, path, json=json, params={"project": project, **(params or {})}
            ),
            detail=detail,
        )
        self.index.invalidate()
        return st.as_dict()


async def get_ctx_operate(
    session: ProjectOperateDep, store: StoreDep, lxd: LXDDep, index: IndexDep
) -> Ctx:
    return Ctx(session, store, lxd, index)


async def get_ctx_manage(
    request: Request,
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
    index: IndexDep,
) -> Ctx:
    # check_project: a second project the change reaches (a move to another project)
    return Ctx(session, store, lxd, index, InBody(request, session, lxd))


async def get_ctx_create(in_body: InBodyDep, store: StoreDep, lxd: LXDDep, index: IndexDep) -> Ctx:
    return Ctx(in_body.session, store, lxd, index, in_body)


CtxOperateDep = Annotated[Ctx, Depends(get_ctx_operate)]
CtxManageDep = Annotated[Ctx, Depends(get_ctx_manage)]
CtxCreateDep = Annotated[Ctx, Depends(get_ctx_create)]


def _inst(name: str) -> str:
    return f"/1.0/instances/{name}"


# --- creating (IN-2) -----------------------------------------------------------


class ImageSource(BaseModel):
    # "local", or a remote: images/ubuntu on LXD 5.21, any image registry on LXD 6.10+
    remote: str = Field(default="local", pattern=NAME, max_length=63)
    alias: str | None = Field(default=None, max_length=200)
    fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{12,64}$")


class NewInstance(BaseModel):
    name: str = Field(pattern=NAME, max_length=63)
    type: Literal["container", "virtual-machine"] = "container"
    project: str = Field(default="default", pattern=NAME, max_length=63)
    target: str | None = Field(default=None, pattern=r"^@?[A-Za-z0-9][A-Za-z0-9._-]*$")
    image: ImageSource | None = None  # none: an empty VM, e.g. to install from an ISO
    description: str = ""
    profiles: list[str] = Field(default_factory=lambda: ["default"])
    config: dict[str, str] = Field(default_factory=dict)
    devices: dict[str, dict[str, str]] = Field(default_factory=dict)
    start: bool = True


def image_source(img: ImageSource, registries: bool = False) -> dict[str, str]:
    """``registries``: the server has image registries (LXD 6.10+), so a remote is one of
    them, by name; otherwise only the simplestreams servers in ``REMOTES``."""
    if img.remote == "local":
        if img.fingerprint:
            return {"type": "image", "fingerprint": img.fingerprint}
        if img.alias:
            return {"type": "image", "alias": img.alias}
        raise HTTPException(status_code=400, detail="choose an image (alias or fingerprint)")
    if not img.alias:
        raise HTTPException(status_code=400, detail="choose a remote image alias")
    if registries:
        return {"type": "image", "alias": img.alias, "image_registry": img.remote}
    if img.remote not in REMOTES:
        raise HTTPException(status_code=400, detail=f"unknown remote; use one of {sorted(REMOTES)}")
    return {
        "type": "image",
        "alias": img.alias,
        "server": REMOTES[img.remote],
        "protocol": "simplestreams",
        "mode": "pull",
    }


@router.post("")
async def create_instance(body: NewInstance, ctx: CtxCreateDep) -> dict[str, Any]:
    await ctx.check_project(body.project)
    if body.image is None and body.type != "virtual-machine":
        raise HTTPException(status_code=400, detail="a container needs an image")
    registries = False
    if body.image is not None and body.image.remote != "local":
        info = await ctx.lxd.get("/1.0")
        registries = "image_registries" in (info.get("api_extensions") or [])
    payload = {
        "name": body.name,
        "type": body.type,
        "description": body.description,
        "profiles": body.profiles,
        "config": body.config,
        "devices": body.devices,
        "start": body.start,
        "source": image_source(body.image, registries) if body.image else {"type": "none"},
    }
    return await ctx.change(
        "instance.create",
        body.project,
        body.name,
        "POST",
        "/1.0/instances",
        json=payload,
        params={"target": body.target} if body.target else None,
        detail={
            "image": (
                f"{body.image.remote}:{body.image.alias or body.image.fingerprint}"
                if body.image
                else "(empty)"
            ),
            "type": body.type,
        },
    )


# --- reading -----------------------------------------------------------------


@router.get("")
async def list_instances(
    scope: ScopeDep, index: IndexDep, fresh: bool = False
) -> list[dict[str, Any]]:
    """``fresh``: skip the short cache (progress views, e.g. during an evacuation)."""
    if fresh:
        index.invalidate()
    return scope.keep([i.as_dict() for i in await index.get()])


@router.get("/{project}/{name}")
async def get_instance(
    project: ProjectName, name: InstName, _: ProjectReadDep, lxd: LXDDep
) -> dict[str, Any]:
    """The full LXD instance (config, devices, expanded_*, state, snapshots)."""
    data: dict[str, Any] = await lxd.get(_inst(name), project=project, recursion=1)
    return data


@router.get("/{project}/{name}/logs")
async def list_logs(
    project: ProjectName, name: InstName, _: ProjectReadDep, lxd: LXDDep
) -> list[str]:
    urls = await lxd.get(f"{_inst(name)}/logs", project=project) or []
    return sorted(str(u).rsplit("/", 1)[-1] for u in urls)


@router.get("/{project}/{name}/logs/{filename}")
async def get_log(
    project: ProjectName, name: InstName, filename: LogName, _: ProjectReadDep, lxd: LXDDep
) -> Response:
    r = await lxd.raw("GET", f"{_inst(name)}/logs/{filename}", params={"project": project})
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=f"LXD: HTTP {r.status_code}")
    return Response(r.content, media_type="text/plain; charset=utf-8")


# --- lifecycle (IN-3) --------------------------------------------------------------


class StateChange(BaseModel):
    action: Literal["start", "stop", "restart", "freeze", "unfreeze"]
    force: bool = False
    stateful: bool = False
    timeout: int = Field(default=30, ge=-1, le=3600)


@router.put("/{project}/{name}/state")
async def change_state(
    project: ProjectName, name: InstName, body: StateChange, ctx: CtxOperateDep
) -> dict[str, Any]:
    return await ctx.change(
        f"instance.{body.action}",
        project,
        name,
        "PUT",
        f"{_inst(name)}/state",
        json=body.model_dump(),
        detail={"force": True} if body.force else None,
    )


@router.delete("/{project}/{name}")
async def delete_instance(
    project: ProjectName, name: InstName, ctx: CtxManageDep, force: bool = False
) -> dict[str, Any]:
    """Delete; with ``force`` a running instance is stopped first (like ``lxc delete -f``).

    The stop and the delete are audited as one change, so a failed stop is recorded too.
    """
    params = {"project": project}

    async def call() -> operations.Started:
        if force:
            st = await ctx.lxd.get(f"{_inst(name)}/state", project=project)
            if st and st.get("status") != "Stopped":
                await operations.run(
                    ctx.lxd,
                    "PUT",
                    f"{_inst(name)}/state",
                    json={"action": "stop", "force": True, "timeout": -1},
                    params=params,
                )
        return await operations.start(ctx.lxd, "DELETE", _inst(name), params=params)

    return await ctx.audit(
        "instance.delete", project, name, call(), detail={"force": True} if force else None
    )


# --- configuration (IN-4) -------------------------------------------------------


class InstanceUpdate(BaseModel):
    """The writable fields of an instance; sent as a whole (LXD PUT semantics)."""

    description: str = ""
    config: dict[str, str] = Field(default_factory=dict)
    devices: dict[str, dict[str, str]] = Field(default_factory=dict)
    profiles: list[str] = Field(default_factory=list)
    ephemeral: bool = False
    stateful: bool = False
    architecture: str | None = None


@router.put("/{project}/{name}")
async def update_instance(
    project: ProjectName, name: InstName, body: InstanceUpdate, ctx: CtxManageDep
) -> dict[str, Any]:
    return await ctx.change(
        "instance.update",
        project,
        name,
        "PUT",
        _inst(name),
        json=body.model_dump(exclude_none=True),
    )


class ConfigPatch(BaseModel):
    config: dict[str, str]


@router.patch("/{project}/{name}/config")
async def patch_config(
    project: ProjectName, name: InstName, body: ConfigPatch, ctx: CtxManageDep
) -> dict[str, Any]:
    """Set some config keys, leaving the rest (used for snapshot schedules)."""
    return await ctx.change(
        "instance.update",
        project,
        name,
        "PATCH",
        _inst(name),
        json={"config": body.config},
        detail={"keys": sorted(body.config)},
    )


# --- rename, move, copy (IN-6) -------------------------------------------------------


class Rename(BaseModel):
    name: str = Field(pattern=NAME, max_length=63)


@router.post("/{project}/{name}/rename")
async def rename(
    project: ProjectName, name: InstName, body: Rename, ctx: CtxManageDep
) -> dict[str, Any]:
    return await ctx.change(
        "instance.rename",
        project,
        name,
        "POST",
        _inst(name),
        json={"name": body.name},
        detail={"to": body.name},
    )


class Move(BaseModel):
    target: str | None = Field(default=None, description="cluster member")
    pool: str | None = Field(default=None, description="storage pool for the root disk")
    project: str | None = Field(
        default=None, pattern=NAME, max_length=63, description="another project (IN-6, 7c)"
    )
    live: bool = False
    instance_only: bool = False  # a project move: leave the snapshots behind


@router.post("/{project}/{name}/move")
async def move(
    project: ProjectName, name: InstName, body: Move, ctx: CtxManageDep
) -> dict[str, Any]:
    """Move to another member, pool or project. A project move needs manage in both
    projects (D83); LXD checks the target's restrictions and limits, and moves a stopped
    instance (``live`` false), with its snapshots unless ``instance_only``."""
    other = body.project if body.project and body.project != project else None
    if not body.target and not body.pool and not other:
        raise HTTPException(status_code=400, detail="give a target member, a pool or a project")
    if other:
        await ctx.check_project(other)
    payload: dict[str, Any] = {"name": name, "migration": True, "live": body.live}
    if body.pool:
        payload["pool"] = body.pool
    if other:
        payload["project"] = other
        payload["instance_only"] = body.instance_only
    return await ctx.change(
        "instance.move",
        project,
        name,
        "POST",
        _inst(name),
        json=payload,
        params={"target": body.target} if body.target else None,
        detail=body.model_dump(exclude_none=True),
    )


class Copy(BaseModel):
    name: str = Field(pattern=NAME, max_length=63)
    snapshot: str | None = Field(default=None, pattern=NAME, description="copy from this snapshot")
    target: str | None = None
    instance_only: bool = True
    start: bool = False


@router.post("/{project}/{name}/copy")
async def copy(
    project: ProjectName, name: InstName, body: Copy, ctx: CtxManageDep
) -> dict[str, Any]:
    source = f"{name}/{body.snapshot}" if body.snapshot else name
    payload = {
        "name": body.name,
        "source": {"type": "copy", "source": source, "instance_only": body.instance_only},
        "start": body.start,
    }
    return await ctx.change(
        "instance.copy",
        project,
        name,
        "POST",
        "/1.0/instances",
        json=payload,
        params={"target": body.target} if body.target else None,
        detail={"to": body.name, "from": source},
    )


# --- snapshots (IN-5) ------------------------------------------------------------


# LXD 6.6+ (instance_snapshots_multi_volume): "all-exclusive" also snapshots (or restores)
# the volumes attached only to this instance; unset is LXD's default, root only
DiskVolumes = Literal["root", "all-exclusive"]


class NewSnapshot(BaseModel):
    name: str = Field(pattern=NAME, max_length=63)
    stateful: bool = False
    expires_at: str | None = None
    disk_volumes_mode: DiskVolumes | None = None


@router.post("/{project}/{name}/snapshots")
async def create_snapshot(
    project: ProjectName, name: InstName, body: NewSnapshot, ctx: CtxOperateDep
) -> dict[str, Any]:
    return await ctx.change(
        "snapshot.create",
        project,
        name,
        "POST",
        f"{_inst(name)}/snapshots",
        json=body.model_dump(exclude_none=True),
        detail={
            "snapshot": body.name,
            **({"volumes": True} if body.disk_volumes_mode == "all-exclusive" else {}),
        },
    )


@router.post("/{project}/{name}/snapshots/{snap}/rename")
async def rename_snapshot(
    project: ProjectName, name: InstName, snap: SnapName, body: Rename, ctx: CtxOperateDep
) -> dict[str, Any]:
    return await ctx.change(
        "snapshot.rename",
        project,
        name,
        "POST",
        f"{_inst(name)}/snapshots/{snap}",
        json={"name": body.name},
        detail={"snapshot": snap, "to": body.name},
    )


@router.delete("/{project}/{name}/snapshots/{snap}")
async def delete_snapshot(
    project: ProjectName, name: InstName, snap: SnapName, ctx: CtxOperateDep
) -> dict[str, Any]:
    return await ctx.change(
        "snapshot.delete",
        project,
        name,
        "DELETE",
        f"{_inst(name)}/snapshots/{snap}",
        detail={"snapshot": snap},
    )


class Restore(BaseModel):
    stateful: bool = False
    disk_volumes_mode: DiskVolumes | None = None


@router.post("/{project}/{name}/snapshots/{snap}/restore")
async def restore_snapshot(
    project: ProjectName, name: InstName, snap: SnapName, body: Restore, ctx: CtxOperateDep
) -> dict[str, Any]:
    return await ctx.change(
        "snapshot.restore",
        project,
        name,
        "PUT",
        _inst(name),
        json={"restore": snap, **body.model_dump(exclude_none=True)},
        detail={"snapshot": snap},
    )


# --- publish (IN-12) ---------------------------------------------------------------


class Publish(BaseModel):
    snapshot: str | None = Field(default=None, pattern=NAME)
    alias: str | None = Field(default=None, max_length=200)
    public: bool = False
    properties: dict[str, str] = Field(default_factory=dict)


@router.post("/{project}/{name}/publish")
async def publish(
    project: ProjectName, name: InstName, body: Publish, ctx: CtxManageDep
) -> dict[str, Any]:
    source: dict[str, str] = (
        {"type": "snapshot", "name": f"{name}/{body.snapshot}"}
        if body.snapshot
        else {"type": "instance", "name": name}
    )
    payload: dict[str, Any] = {
        "source": source,
        "public": body.public,
        "properties": body.properties,
    }
    if body.alias:
        payload["aliases"] = [{"name": body.alias}]
    return await ctx.change(
        "image.publish",
        project,
        name,
        "POST",
        "/1.0/images",
        json=payload,
        detail={"from": source["name"], "alias": body.alias},
    )
