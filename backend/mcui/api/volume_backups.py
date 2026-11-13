"""Custom volume backups (ST-4, Phase 7b) and ISO uploads (IM-3, Phase 7d).

Backups: list, create, rename, delete, download, import.

As for instances (``mcui.api.backups``): operate for backups and downloads, manage in the
target project for an import or an ISO upload (D83). Volumes on local pools live on one
member, passed as ``target`` (D55). A restricted project refuses backups unless
``restricted.backups=allow``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from mcui.api.audited import audited
from mcui.api.backups import IMPORT_TIMEOUT, BackupName, Compression, _done, stream_export
from mcui.api.deps import InBodyDep, LXDDep, ProjectOperateDep, ProjectReadDep, StoreDep
from mcui.api.instances import NAME, ProjectName
from mcui.api.manage import ObjName, Rename
from mcui.api.networks import MemberName
from mcui.api.volumes import _change, _params, _vol
from mcui.lxd import operations

router = APIRouter(prefix="/api/volumes")


def _backups(pool: str, name: str) -> str:
    return f"{_vol(pool, name)}/backups"


@router.get("/{project}/{pool}/{name}/backups")
async def list_backups(
    project: ProjectName,
    pool: ObjName,
    name: ObjName,
    _: ProjectReadDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> list[dict[str, Any]]:
    items = await lxd.query(
        "GET", _backups(pool, name), params={**_params(project, target), "recursion": 1}
    )
    return sorted(items or [], key=lambda b: str(b.get("created_at", "")), reverse=True)


class NewVolumeBackup(BaseModel):
    name: str = Field(pattern=NAME, max_length=63)
    expires_at: str | None = None
    volume_only: bool = False
    optimized_storage: bool = False
    compression_algorithm: Compression | None = None


@router.post("/{project}/{pool}/{name}/backups")
async def create_backup(
    project: ProjectName,
    pool: ObjName,
    name: ObjName,
    body: NewVolumeBackup,
    session: ProjectOperateDep,
    store: StoreDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "volume.backup.create",
        f"{project}/{pool}/{name}",
        "POST",
        _backups(pool, name),
        _params(project, target),
        json=body.model_dump(exclude_none=True),
        detail={"backup": body.name},
    )


@router.post("/{project}/{pool}/{name}/backups/{backup}/rename")
async def rename_backup(
    project: ProjectName,
    pool: ObjName,
    name: ObjName,
    backup: BackupName,
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
        "volume.backup.rename",
        f"{project}/{pool}/{name}",
        "POST",
        f"{_backups(pool, name)}/{backup}",
        _params(project, target),
        json={"name": body.name},
        detail={"backup": backup, "to": body.name},
    )


@router.delete("/{project}/{pool}/{name}/backups/{backup}")
async def delete_backup(
    project: ProjectName,
    pool: ObjName,
    name: ObjName,
    backup: BackupName,
    session: ProjectOperateDep,
    store: StoreDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "volume.backup.delete",
        f"{project}/{pool}/{name}",
        "DELETE",
        f"{_backups(pool, name)}/{backup}",
        _params(project, target),
        detail={"backup": backup},
    )


@router.get("/{project}/{pool}/{name}/backups/{backup}/export")
async def export_backup(
    project: ProjectName,
    pool: ObjName,
    name: ObjName,
    backup: BackupName,
    session: ProjectOperateDep,
    store: StoreDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> Response:
    return await stream_export(
        lxd,
        f"{_backups(pool, name)}/{backup}/export",
        _params(project, target),
        f"{name}-{backup}",
        audited(
            session,
            store,
            "volume.backup.export",
            f"{project}/{pool}/{name}",
            _done(),
            detail={"backup": backup},
        ),
    )


@router.post("/import")
async def import_backup(
    request: Request,
    guard: InBodyDep,
    store: StoreDep,
    lxd: LXDDep,
    pool: Annotated[str, Query(pattern=NAME, max_length=63)],
    project: Annotated[str, Query(pattern=NAME, max_length=63)] = "default",
    name: Annotated[str | None, Query(pattern=NAME, max_length=63)] = None,
    target: MemberName = None,
) -> dict[str, Any]:
    """Create a custom volume from a backup tarball sent as the raw request body (streamed);
    ``name`` overrides the backup's own."""
    return await _upload(
        request, guard, store, lxd, "volume.backup.import", project, pool, name, target
    )


async def _upload(
    request: Request,
    guard: InBodyDep,
    store: StoreDep,
    lxd: LXDDep,
    action: str,
    project: str,
    pool: str,
    name: str | None,
    target: str | None,
    extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Stream the request body to LXD as a new custom volume in ``pool``."""
    await guard.check(project)
    if request.headers.get("content-length") == "0":
        raise HTTPException(status_code=400, detail="send the file as the body")
    headers = {"Content-Type": "application/octet-stream", **(extra or {})}
    if name:
        headers["X-LXD-name"] = name
    st = await audited(
        guard.session,
        store,
        action,
        f"{project}/{pool}/{name or '(from backup)'}",
        operations.start(
            lxd,
            "POST",
            _vol(pool),
            params=_params(project, target),
            headers=headers,
            content=request.stream(),
            timeout=IMPORT_TIMEOUT,
        ),
    )
    return st.as_dict()


@router.post("/iso")
async def upload_iso(
    request: Request,
    guard: InBodyDep,
    store: StoreDep,
    lxd: LXDDep,
    pool: Annotated[str, Query(pattern=NAME, max_length=63)],
    name: Annotated[str, Query(pattern=NAME, max_length=63)],
    project: Annotated[str, Query(pattern=NAME, max_length=63)] = "default",
    target: MemberName = None,
) -> dict[str, Any]:
    """An ISO image sent as the raw request body becomes a custom volume of content type
    ``iso`` (LXD ``custom_volume_iso``), to attach to VMs as a CD."""
    return await _upload(
        request,
        guard,
        store,
        lxd,
        "volume.iso.upload",
        project,
        pool,
        name,
        target,
        {"X-LXD-type": "iso"},
    )
