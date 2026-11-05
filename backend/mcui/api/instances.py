"""Instances: list, detail, lifecycle, config, rename/copy/move, snapshots, logs, publish.

IN-1, IN-3, IN-4, IN-5, IN-6, IN-10, IN-12. Every change needs the admin role
and CSRF token, and is audited with its LXD operation (D42).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Response
from pydantic import BaseModel, Field

from mcui.api.audited import audited
from mcui.api.deps import AdminDep, LXDDep, ServicesDep, SessionDep, StoreDep
from mcui.auth.store import Session, Store
from mcui.lxd import operations
from mcui.lxd.client import LXDClient
from mcui.lxd.instances import InstanceIndex

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
        self, session: Session, store: Store, lxd: LXDClient, index: InstanceIndex
    ) -> None:
        self.session, self.store, self.lxd, self.index = session, store, lxd, index

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


async def get_ctx(session: AdminDep, store: StoreDep, lxd: LXDDep, index: IndexDep) -> Ctx:
    return Ctx(session, store, lxd, index)


CtxDep = Annotated[Ctx, Depends(get_ctx)]


def _inst(name: str) -> str:
    return f"/1.0/instances/{name}"


# --- reading -----------------------------------------------------------------


@router.get("")
async def list_instances(_: SessionDep, index: IndexDep) -> list[dict[str, Any]]:
    return [i.as_dict() for i in await index.get()]


@router.get("/{project}/{name}")
async def get_instance(
    project: ProjectName, name: InstName, _: SessionDep, lxd: LXDDep
) -> dict[str, Any]:
    """The full LXD instance (config, devices, expanded_*, state, snapshots)."""
    data: dict[str, Any] = await lxd.get(_inst(name), project=project, recursion=1)
    return data


@router.get("/{project}/{name}/logs")
async def list_logs(project: ProjectName, name: InstName, _: SessionDep, lxd: LXDDep) -> list[str]:
    urls = await lxd.get(f"{_inst(name)}/logs", project=project) or []
    return sorted(str(u).rsplit("/", 1)[-1] for u in urls)


@router.get("/{project}/{name}/logs/{filename}")
async def get_log(
    project: ProjectName, name: InstName, filename: LogName, _: SessionDep, lxd: LXDDep
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
    project: ProjectName, name: InstName, body: StateChange, ctx: CtxDep
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
    project: ProjectName, name: InstName, ctx: CtxDep, force: bool = False
) -> dict[str, Any]:
    """Delete; with ``force`` a running instance is stopped first (like ``lxc delete -f``)."""
    if force:
        st = await ctx.lxd.get(f"{_inst(name)}/state", project=project)
        if st and st.get("status") != "Stopped":
            await operations.run(
                ctx.lxd,
                "PUT",
                f"{_inst(name)}/state",
                json={"action": "stop", "force": True, "timeout": -1},
                params={"project": project},
            )
    return await ctx.change(
        "instance.delete",
        project,
        name,
        "DELETE",
        _inst(name),
        detail={"force": True} if force else None,
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
    project: ProjectName, name: InstName, body: InstanceUpdate, ctx: CtxDep
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
    project: ProjectName, name: InstName, body: ConfigPatch, ctx: CtxDep
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
async def rename(project: ProjectName, name: InstName, body: Rename, ctx: CtxDep) -> dict[str, Any]:
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
    live: bool = False


@router.post("/{project}/{name}/move")
async def move(project: ProjectName, name: InstName, body: Move, ctx: CtxDep) -> dict[str, Any]:
    if not body.target and not body.pool:
        raise HTTPException(status_code=400, detail="give a target member or a pool")
    payload: dict[str, Any] = {"name": name, "migration": True, "live": body.live}
    if body.pool:
        payload["pool"] = body.pool
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
async def copy(project: ProjectName, name: InstName, body: Copy, ctx: CtxDep) -> dict[str, Any]:
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


class NewSnapshot(BaseModel):
    name: str = Field(pattern=NAME, max_length=63)
    stateful: bool = False
    expires_at: str | None = None


@router.post("/{project}/{name}/snapshots")
async def create_snapshot(
    project: ProjectName, name: InstName, body: NewSnapshot, ctx: CtxDep
) -> dict[str, Any]:
    return await ctx.change(
        "snapshot.create",
        project,
        name,
        "POST",
        f"{_inst(name)}/snapshots",
        json=body.model_dump(exclude_none=True),
        detail={"snapshot": body.name},
    )


@router.post("/{project}/{name}/snapshots/{snap}/rename")
async def rename_snapshot(
    project: ProjectName, name: InstName, snap: SnapName, body: Rename, ctx: CtxDep
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
    project: ProjectName, name: InstName, snap: SnapName, ctx: CtxDep
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


@router.post("/{project}/{name}/snapshots/{snap}/restore")
async def restore_snapshot(
    project: ProjectName, name: InstName, snap: SnapName, body: Restore, ctx: CtxDep
) -> dict[str, Any]:
    return await ctx.change(
        "snapshot.restore",
        project,
        name,
        "PUT",
        _inst(name),
        json={"restore": snap, "stateful": body.stateful},
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
    project: ProjectName, name: InstName, body: Publish, ctx: CtxDep
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
