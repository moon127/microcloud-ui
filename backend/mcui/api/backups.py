"""Instance backups (IN-13, Phase 7a): list, create, rename, delete, download, import.

Creating, renaming, deleting and downloading need operate in the instance's project (a
download holds its files, as the Files tab does); importing creates an instance, so it
needs manage in the target project (D83). A restricted project refuses backups unless
``restricted.backups=allow`` (seen live on LXD 6.10). Downloads and imports stream through
the backend; the tarball lives on the member that holds the instance.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from mcui.api.audited import audited
from mcui.api.deps import InBodyDep, LXDDep, ProjectReadDep, StoreDep
from mcui.api.files import _error
from mcui.api.instances import NAME, CtxOperateDep, IndexDep, InstName, ProjectName, Rename
from mcui.lxd import operations

router = APIRouter(prefix="/api/instances")

BackupName = Annotated[str, Path(pattern=NAME, max_length=63)]
Compression = Literal["gzip", "zstd", "xz", "bzip2", "none"]
# LXD takes a while to unpack a large tarball before it answers an import
IMPORT_TIMEOUT = 600.0

# a tarball's first bytes: which file name to offer for the download
MAGIC = (
    (b"\x1f\x8b", ".tar.gz"),
    (b"\x28\xb5\x2f\xfd", ".tar.zst"),
    (b"\xfd7zXZ", ".tar.xz"),
    (b"BZh", ".tar.bz2"),
)


def suffix(head: bytes) -> str:
    return next((ext for magic, ext in MAGIC if head.startswith(magic)), ".tar")


def _backups(name: str) -> str:
    return f"/1.0/instances/{name}/backups"


@router.get("/{project}/{name}/backups")
async def list_backups(
    project: ProjectName, name: InstName, _: ProjectReadDep, lxd: LXDDep
) -> list[dict[str, Any]]:
    items = await lxd.query("GET", _backups(name), params={"project": project, "recursion": 1})
    return sorted(items or [], key=lambda b: str(b.get("created_at", "")), reverse=True)


class NewBackup(BaseModel):
    name: str = Field(pattern=NAME, max_length=63)
    expires_at: str | None = None
    instance_only: bool = False
    optimized_storage: bool = False
    compression_algorithm: Compression | None = None  # unset: the server's default


@router.post("/{project}/{name}/backups")
async def create_backup(
    project: ProjectName, name: InstName, body: NewBackup, ctx: CtxOperateDep
) -> dict[str, Any]:
    return await ctx.change(
        "backup.create",
        project,
        name,
        "POST",
        _backups(name),
        json=body.model_dump(exclude_none=True),
        detail={"backup": body.name},
    )


@router.post("/{project}/{name}/backups/{backup}/rename")
async def rename_backup(
    project: ProjectName, name: InstName, backup: BackupName, body: Rename, ctx: CtxOperateDep
) -> dict[str, Any]:
    return await ctx.change(
        "backup.rename",
        project,
        name,
        "POST",
        f"{_backups(name)}/{backup}",
        json={"name": body.name},
        detail={"backup": backup, "to": body.name},
    )


@router.delete("/{project}/{name}/backups/{backup}")
async def delete_backup(
    project: ProjectName, name: InstName, backup: BackupName, ctx: CtxOperateDep
) -> dict[str, Any]:
    return await ctx.change(
        "backup.delete",
        project,
        name,
        "DELETE",
        f"{_backups(name)}/{backup}",
        detail={"backup": backup},
    )


@router.get("/{project}/{name}/backups/{backup}/export")
async def export_backup(
    project: ProjectName, name: InstName, backup: BackupName, ctx: CtxOperateDep
) -> Response:
    """Stream the backup tarball; audited, since it carries the instance's files."""
    r = await ctx.lxd.open_stream(
        "GET", f"{_backups(name)}/{backup}/export", params={"project": project}
    )
    if r.status_code != 200:
        await r.aread()
        await r.aclose()
        raise _error(r)
    chunks = r.aiter_raw()
    head = await anext(chunks, b"")

    async def body() -> Any:
        yield head
        async for chunk in chunks:
            yield chunk

    await audited(
        ctx.session,
        ctx.store,
        "backup.export",
        f"{project}/{name}",
        _done(),
        detail={"backup": backup},
    )
    headers = {"Content-Disposition": f'attachment; filename="{name}-{backup}{suffix(head)}"'}
    if "content-length" in r.headers:
        headers["Content-Length"] = r.headers["content-length"]
    return StreamingResponse(
        body(),
        media_type="application/octet-stream",
        headers=headers,
        background=BackgroundTask(r.aclose),
    )


async def _done() -> operations.Started:
    return operations.Started(operation=None)


@router.post("/import")
async def import_backup(
    request: Request,
    guard: InBodyDep,
    store: StoreDep,
    lxd: LXDDep,
    index: IndexDep,
    project: Annotated[str, Query(pattern=NAME, max_length=63)] = "default",
    name: Annotated[str | None, Query(pattern=NAME, max_length=63)] = None,
    pool: Annotated[str | None, Query(pattern=NAME, max_length=63)] = None,
) -> dict[str, Any]:
    """Create an instance from a backup tarball sent as the raw request body (streamed).

    ``name`` and ``pool`` override the backup's own; LXD keeps everything else from it.
    """
    await guard.check(project)
    if request.headers.get("content-length") == "0":
        raise HTTPException(status_code=400, detail="send the backup file as the body")
    headers = {"Content-Type": "application/octet-stream"}
    if name:
        headers["X-LXD-name"] = name
    if pool:
        headers["X-LXD-pool"] = pool
    st = await audited(
        guard.session,
        store,
        "backup.import",
        f"{project}/{name or '(from backup)'}",
        operations.start(
            lxd,
            "POST",
            "/1.0/instances",
            params={"project": project},
            headers=headers,
            content=request.stream(),
            timeout=IMPORT_TIMEOUT,
        ),
        detail={"pool": pool} if pool else None,
    )
    index.invalidate()
    return st.as_dict()
