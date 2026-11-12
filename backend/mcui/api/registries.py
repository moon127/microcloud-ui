"""Image registries (LXD 6.10+, ``image_registries``; Phase 5b).

LXD 6.10 keeps image servers server-side, in the cluster database: built-in ones
(``images``, ``ubuntu``, ``ubuntu-daily`` and the minimal variants) and any added here.
The create-instance picker lists their images through LXD (``catalog.remote_images``)
and creates instances with ``source.image_registry``. A registry added here is a
SimpleStreams server (LXD infers the protocol from ``url``, which must be HTTPS).
Every change: admin + CSRF, audited.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from mcui.api.acls import _change
from mcui.api.deps import AdminDep, LXDDep, SessionDep, StoreDep
from mcui.api.instances import NAME
from mcui.api.manage import ObjName

router = APIRouter(prefix="/api/image-registries")


class NewRegistry(BaseModel):
    name: str = Field(pattern=NAME, max_length=63)
    url: str = Field(pattern=r"^https://[^\s/$.?#][^\s]*$", max_length=500)
    description: str = ""


@router.get("")
async def list_registries(_: SessionDep, lxd: LXDDep) -> list[dict[str, Any]]:
    return list(await lxd.get("/1.0/image-registries", recursion=1) or [])


@router.post("")
async def create_registry(
    body: NewRegistry, session: AdminDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "image-registry.create",
        body.name,
        "POST",
        "/1.0/image-registries",
        {},
        json={"name": body.name, "description": body.description, "config": {"url": body.url}},
        detail={"url": body.url},
    )


@router.delete("/{name}")
async def delete_registry(
    name: ObjName, session: AdminDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "image-registry.delete",
        name,
        "DELETE",
        f"/1.0/image-registries/{name}",
        {},
    )
