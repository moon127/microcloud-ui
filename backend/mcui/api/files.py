"""File browser for instances (IN-11): list, download, upload, mkdir, delete.

Uses LXD's ``/1.0/instances/{name}/files``. Listings return names only, so each
entry's type, mode, owner and size come from a HEAD request (bounded, 16 at a
time, first 500 entries). Downloads and uploads stream through the backend.
"""

from __future__ import annotations

import asyncio
import posixpath
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from mcui.api.audited import audited
from mcui.api.deps import AdminDep, LXDDep, SessionDep, StoreDep
from mcui.api.instances import InstName, ProjectName
from mcui.lxd import operations
from mcui.lxd.client import LXDClient, LXDError

router = APIRouter(prefix="/api/instances/{project}/{name}/files")

MAX_ENTRIES = 500
PARALLEL = 16

FilePath = Annotated[str, Query(min_length=1, max_length=4096)]


def clean(path: str) -> str:
    if not path.startswith("/") or "\x00" in path:
        raise HTTPException(status_code=400, detail="path must be absolute")
    return posixpath.normpath(path) if path != "/" else "/"


def _meta(headers: Any) -> dict[str, Any]:
    size = headers.get("content-length")
    return {
        "type": headers.get("x-lxd-type", ""),
        "mode": headers.get("x-lxd-mode", ""),
        "uid": headers.get("x-lxd-uid", ""),
        "gid": headers.get("x-lxd-gid", ""),
        "modified": headers.get("x-lxd-modified", ""),
        "size": int(size) if size and size.isdigit() else None,
    }


def _message(r: Any) -> str:
    """LXD's error text from a response, or the HTTP status."""
    try:
        msg = r.json().get("error")
    except ValueError:
        msg = None
    return str(msg or f"HTTP {r.status_code}")


def _error(r: Any) -> HTTPException:
    status = 404 if r.status_code == 404 else 502
    return HTTPException(status_code=status, detail=f"LXD: {_message(r)}")


async def _head(lxd: LXDClient, name: str, project: str, path: str) -> dict[str, Any] | None:
    r = await lxd.raw(
        "HEAD", f"/1.0/instances/{name}/files", params={"project": project, "path": path}
    )
    return _meta(r.headers) if r.status_code == 200 else None


@router.get("")
async def browse(
    project: ProjectName, name: InstName, path: FilePath, _: SessionDep, lxd: LXDDep
) -> dict[str, Any]:
    path = clean(path)
    files = f"/1.0/instances/{name}/files"
    params = {"project": project, "path": path}
    r = await lxd.raw("GET", files, params=params)
    if r.status_code != 200:
        raise _error(r)
    meta = _meta(r.headers)
    if meta["type"] == "symlink":
        target = r.content.decode("utf-8", "replace")
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
        return {"path": path, **meta, "target": target, "resolved": resolved}
    if meta["type"] != "directory":
        return {
            "path": path,
            **meta,
            "size": meta["size"] if meta["size"] is not None else len(r.content),
        }

    names = sorted(r.json().get("metadata") or [])
    sem = asyncio.Semaphore(PARALLEL)

    async def entry(n: str) -> dict[str, Any]:
        async with sem:
            m = await _head(lxd, name, project, posixpath.join(path, n))
        return {"name": n, **(m or {"type": "unknown"})}

    detailed = await asyncio.gather(*(entry(n) for n in names[:MAX_ENTRIES]))
    rest = [{"name": n, "type": "unknown"} for n in names[MAX_ENTRIES:]]
    entries = sorted(detailed, key=lambda e: (e["type"] != "directory", e["name"])) + rest
    return {"path": path, **meta, "entries": entries, "truncated": len(names) > MAX_ENTRIES}


@router.get("/download")
async def download(
    project: ProjectName, name: InstName, path: FilePath, _: SessionDep, lxd: LXDDep
) -> Response:
    path = clean(path)
    r = await lxd.open_stream(
        "GET", f"/1.0/instances/{name}/files", params={"project": project, "path": path}
    )
    if r.status_code != 200 or r.headers.get("x-lxd-type") != "file":
        await r.aread()
        await r.aclose()
        if r.status_code == 200:
            raise HTTPException(status_code=400, detail="not a regular file")
        raise _error(r)
    filename = posixpath.basename(path).replace('"', "")
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if "content-length" in r.headers:
        headers["Content-Length"] = r.headers["content-length"]
    return StreamingResponse(
        r.aiter_raw(),
        media_type="application/octet-stream",
        headers=headers,
        background=BackgroundTask(r.aclose),
    )


async def _write(
    lxd: LXDClient, name: str, project: str, path: str, headers: dict[str, str], content: Any = None
) -> operations.Started:
    r = await lxd.raw(
        "POST",
        f"/1.0/instances/{name}/files",
        params={"project": project, "path": path},
        headers=headers,
        content=content,
    )
    if r.status_code != 200:
        raise LXDError(_message(r), r.status_code)
    return operations.Started(operation=None)


@router.post("/upload")
async def upload(
    project: ProjectName,
    name: InstName,
    path: FilePath,
    request: Request,
    session: AdminDep,
    store: StoreDep,
    lxd: LXDDep,
    mode: Annotated[str, Query(pattern=r"^[0-7]{3,4}$")] = "0644",
) -> dict[str, Any]:
    """Write the request body to ``path`` (overwriting), streamed."""
    path = clean(path)
    headers = {
        "X-LXD-type": "file",
        "X-LXD-write": "overwrite",
        "X-LXD-mode": mode,
        "Content-Type": "application/octet-stream",
    }
    st = await audited(
        session,
        store,
        "file.upload",
        f"{project}/{name}",
        _write(lxd, name, project, path, headers, request.stream()),
        detail={"path": path},
    )
    return st.as_dict()


@router.post("/mkdir")
async def mkdir(
    project: ProjectName,
    name: InstName,
    path: FilePath,
    session: AdminDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    path = clean(path)
    headers = {"X-LXD-type": "directory", "X-LXD-mode": "0755"}
    st = await audited(
        session,
        store,
        "file.mkdir",
        f"{project}/{name}",
        _write(lxd, name, project, path, headers),
        detail={"path": path},
    )
    return st.as_dict()


@router.delete("")
async def delete(
    project: ProjectName,
    name: InstName,
    path: FilePath,
    session: AdminDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    """Delete a file, symlink or empty directory."""
    path = clean(path)
    if path == "/":
        raise HTTPException(status_code=400, detail="refusing to delete /")
    st = await audited(
        session,
        store,
        "file.delete",
        f"{project}/{name}",
        operations.start(
            lxd, "DELETE", f"/1.0/instances/{name}/files", params={"project": project, "path": path}
        ),
        detail={"path": path},
    )
    return st.as_dict()
