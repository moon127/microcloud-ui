"""Profiles (PR-1), images (IM-1, IM-2) and projects (PJ-1).

Listing endpoints live in ``catalog`` (they also feed the create wizard); this
module adds detail views and changes. Every change: admin + CSRF, audited (D42).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field

from mcui.api.audited import audited
from mcui.api.deps import (
    AdminDep,
    InBodyDep,
    LXDDep,
    ProjectManageDep,
    ProjectReadDep,
    StoreDep,
)
from mcui.api.instances import NAME, ProjectName
from mcui.auth.store import Session, Store
from mcui.lxd import operations
from mcui.lxd.client import LXDClient
from mcui.lxd.remotes import REMOTES

router = APIRouter(prefix="/api")

ObjName = Annotated[str, Path(pattern=NAME, max_length=63)]
Fingerprint = Annotated[str, Path(pattern=r"^[0-9a-f]{12,64}$")]
UPLOAD_WAITS = 20  # x 30 s: give large imports up to 10 minutes before adding the alias
AliasName = Annotated[str, Path(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/:-]*$", max_length=200)]


async def _change(
    session: Session,
    store: Store,
    lxd: LXDClient,
    action: str,
    target: str,
    method: str,
    path: str,
    *,
    project: str | None = None,
    json: Any = None,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    st = await audited(
        session,
        store,
        action,
        target,
        operations.start(
            lxd, method, path, json=json, params={"project": project} if project else None
        ),
        detail=detail,
    )
    return st.as_dict()


class Rename(BaseModel):
    name: str = Field(pattern=NAME, max_length=63)


# --- profiles --------------------------------------------------------------------


class ProfileBody(BaseModel):
    description: str = ""
    config: dict[str, str] = Field(default_factory=dict)
    devices: dict[str, dict[str, str]] = Field(default_factory=dict)


class NewProfile(ProfileBody):
    name: str = Field(pattern=NAME, max_length=63)
    project: str = Field(default="default", pattern=NAME, max_length=63)


@router.get("/profiles/{project}/{name}")
async def get_profile(
    project: ProjectName, name: ObjName, _: ProjectReadDep, lxd: LXDDep
) -> dict[str, Any]:
    data: dict[str, Any] = await lxd.get(f"/1.0/profiles/{name}", project=project)
    return data


@router.post("/profiles")
async def create_profile(
    body: NewProfile, guard: InBodyDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    await guard.check(body.project)
    session = guard.session
    return await _change(
        session,
        store,
        lxd,
        "profile.create",
        f"{body.project}/{body.name}",
        "POST",
        "/1.0/profiles",
        project=body.project,
        json=body.model_dump(exclude={"project"}),
    )


@router.put("/profiles/{project}/{name}")
async def update_profile(
    project: ProjectName,
    name: ObjName,
    body: ProfileBody,
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "profile.update",
        f"{project}/{name}",
        "PUT",
        f"/1.0/profiles/{name}",
        project=project,
        json=body.model_dump(),
    )


@router.post("/profiles/{project}/{name}/rename")
async def rename_profile(
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
        "profile.rename",
        f"{project}/{name}",
        "POST",
        f"/1.0/profiles/{name}",
        project=project,
        json={"name": body.name},
        detail={"to": body.name},
    )


@router.delete("/profiles/{project}/{name}")
async def delete_profile(
    project: ProjectName, name: ObjName, session: ProjectManageDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "profile.delete",
        f"{project}/{name}",
        "DELETE",
        f"/1.0/profiles/{name}",
        project=project,
    )


# --- images ----------------------------------------------------------------------


@router.get("/images/{project}/{fingerprint}")
async def get_image(
    project: ProjectName, fingerprint: Fingerprint, _: ProjectReadDep, lxd: LXDDep
) -> dict[str, Any]:
    data: dict[str, Any] = await lxd.get(f"/1.0/images/{fingerprint}", project=project)
    return data


class ImageUpdate(BaseModel):
    properties: dict[str, str] = Field(default_factory=dict)
    public: bool = False
    auto_update: bool = False


@router.put("/images/{project}/{fingerprint}")
async def update_image(
    project: ProjectName,
    fingerprint: Fingerprint,
    body: ImageUpdate,
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "image.update",
        f"{project}/{fingerprint[:12]}",
        "PUT",
        f"/1.0/images/{fingerprint}",
        project=project,
        json=body.model_dump(),
    )


@router.delete("/images/{project}/{fingerprint}")
async def delete_image(
    project: ProjectName,
    fingerprint: Fingerprint,
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "image.delete",
        f"{project}/{fingerprint[:12]}",
        "DELETE",
        f"/1.0/images/{fingerprint}",
        project=project,
    )


class NewAlias(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/:-]*$", max_length=200)
    description: str = ""


@router.post("/images/{project}/{fingerprint}/aliases")
async def add_alias(
    project: ProjectName,
    fingerprint: Fingerprint,
    body: NewAlias,
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "image.alias.create",
        f"{project}/{fingerprint[:12]}",
        "POST",
        "/1.0/images/aliases",
        project=project,
        json={"name": body.name, "target": fingerprint, "description": body.description},
        detail={"alias": body.name},
    )


@router.delete("/images/{project}/aliases/{alias:path}")
async def delete_alias(
    project: ProjectName, alias: AliasName, session: ProjectManageDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "image.alias.delete",
        f"{project}/{alias}",
        "DELETE",
        f"/1.0/images/aliases/{alias}",
        project=project,
    )


class ImageImport(BaseModel):
    project: str = Field(default="default", pattern=NAME, max_length=63)
    remote: str
    alias: str = Field(min_length=1, max_length=200)
    local_alias: str | None = Field(
        default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._/:-]*$", max_length=200
    )
    auto_update: bool = True
    vm: bool = False


@router.post("/images/import")
async def import_image(
    body: ImageImport, guard: InBodyDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    await guard.check(body.project)
    session = guard.session
    if body.remote not in REMOTES:
        raise HTTPException(status_code=400, detail=f"remote must be one of {sorted(REMOTES)}")
    payload: dict[str, Any] = {
        "source": {
            "type": "image",
            "mode": "pull",
            "server": REMOTES[body.remote],
            "protocol": "simplestreams",
            "alias": body.alias,
            **({"image_type": "virtual-machine"} if body.vm else {}),
        },
        "auto_update": body.auto_update,
    }
    if body.local_alias:
        payload["aliases"] = [{"name": body.local_alias}]
    return await _change(
        session,
        store,
        lxd,
        "image.import",
        f"{body.project}/{body.remote}:{body.alias}",
        "POST",
        "/1.0/images",
        project=body.project,
        json=payload,
        detail={"local_alias": body.local_alias, "vm": body.vm},
    )


@router.post("/images/upload")
async def upload_image(
    request: Request,
    guard: InBodyDep,
    store: StoreDep,
    lxd: LXDDep,
    project: Annotated[str, Query(pattern=NAME, max_length=63)] = "default",
    alias: Annotated[
        str | None, Query(pattern=r"^[A-Za-z0-9][A-Za-z0-9._/:-]*$", max_length=200)
    ] = None,
    filename: Annotated[str, Query(max_length=255)] = "image.tar.gz",
) -> dict[str, Any]:
    """Import a unified image tarball sent as the raw request body (streamed)."""
    await guard.check(project)
    session = guard.session

    call = operations.start(
        lxd,
        "POST",
        "/1.0/images",
        params={"project": project},
        headers={"Content-Type": "application/octet-stream", "X-LXD-filename": filename},
        content=request.stream(),
    )
    st = await audited(
        session, store, "image.upload", f"{project}/{filename}", call, detail={"alias": alias}
    )
    if alias and st.operation is not None:
        # the alias needs the fingerprint, known once the import finishes
        op = st.operation
        for _ in range(UPLOAD_WAITS):
            op = await operations.wait(lxd, str(op["id"]))
            if op.get("status") not in ("Pending", "Running"):
                break
        fp = (op.get("metadata") or {}).get("fingerprint")
        if op.get("status") == "Success" and fp:
            await _change(
                session,
                store,
                lxd,
                "image.alias.create",
                f"{project}/{fp[:12]}",
                "POST",
                "/1.0/images/aliases",
                project=project,
                json={"name": alias, "target": fp},
                detail={"alias": alias},
            )
        st.operation = op
    return st.as_dict()


# --- projects --------------------------------------------------------------------


class ProjectBody(BaseModel):
    description: str = ""
    config: dict[str, str] = Field(default_factory=dict)


class NewProject(ProjectBody):
    name: str = Field(pattern=NAME, max_length=63)


@router.get("/projects/{project}")
async def get_project(project: ObjName, _: ProjectReadDep, lxd: LXDDep) -> dict[str, Any]:
    data: dict[str, Any] = await lxd.get(f"/1.0/projects/{project}")
    return data


@router.post("/projects")
async def create_project(
    body: NewProject, session: AdminDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "project.create",
        body.name,
        "POST",
        "/1.0/projects",
        json=body.model_dump(),
    )


@router.put("/projects/{name}")
async def update_project(
    name: ObjName, body: ProjectBody, session: AdminDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "project.update",
        name,
        "PUT",
        f"/1.0/projects/{name}",
        json=body.model_dump(),
    )


@router.post("/projects/{name}/rename")
async def rename_project(
    name: ObjName, body: Rename, session: AdminDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    if name == "default":
        raise HTTPException(status_code=400, detail="the default project can't be renamed")
    return await _change(
        session,
        store,
        lxd,
        "project.rename",
        name,
        "POST",
        f"/1.0/projects/{name}",
        json={"name": body.name},
        detail={"to": body.name},
    )


@router.delete("/projects/{name}")
async def delete_project(
    name: ObjName, session: AdminDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    if name == "default":
        raise HTTPException(status_code=400, detail="the default project can't be deleted")
    return await _change(
        session, store, lxd, "project.delete", name, "DELETE", f"/1.0/projects/{name}"
    )
