"""Network ACLs (NW-4, CRUD) and network forwards (NW-5).

ACLs are attached through a network's ``security.acls`` keys (edited on the
network page) or an OVN NIC's; this module only manages the ACLs themselves.
Forwards are keyed by their listen address. Every change: admin + CSRF,
audited (D42, D53).
"""

from __future__ import annotations

import ipaddress
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field, field_validator

from mcui.api.audited import audited
from mcui.api.deps import InBodyDep, LXDDep, ProjectManageDep, ProjectReadDep, ScopeDep, StoreDep
from mcui.api.instances import NAME, ProjectName
from mcui.api.manage import ObjName, Rename
from mcui.api.networks import MemberName
from mcui.auth.store import Session, Store
from mcui.lxd import operations
from mcui.lxd.client import LXDClient

router = APIRouter(prefix="/api")


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


# --- ACLs ------------------------------------------------------------------------


class Rule(BaseModel):
    action: Literal["allow", "allow-stateless", "drop", "reject"]
    state: Literal["enabled", "disabled", "logged"] = "enabled"
    description: str = ""
    source: str = ""
    destination: str = ""
    protocol: Literal["", "tcp", "udp", "icmp4", "icmp6"] = ""
    source_port: str = ""
    destination_port: str = ""
    icmp_type: str = ""
    icmp_code: str = ""


class AclBody(BaseModel):
    description: str = ""
    ingress: list[Rule] = Field(default_factory=list)
    egress: list[Rule] = Field(default_factory=list)
    config: dict[str, str] = Field(default_factory=dict)


class NewAcl(AclBody):
    name: str = Field(pattern=NAME, max_length=63)
    project: str = Field(default="default", pattern=NAME, max_length=63)


def _acl(name: str) -> str:
    return f"/1.0/network-acls/{name}"


@router.get("/network-acls")
async def list_acls(scope: ScopeDep, lxd: LXDDep, project: str = "default") -> list[dict[str, Any]]:
    scope.require(project)
    data = await lxd.get("/1.0/network-acls", recursion=1, project=project)
    return list(data or [])


@router.get("/network-acls/{project}/{name}")
async def get_acl(
    project: ProjectName, name: ObjName, _: ProjectReadDep, lxd: LXDDep
) -> dict[str, Any]:
    data: dict[str, Any] = await lxd.get(_acl(name), project=project)
    return data


@router.post("/network-acls")
async def create_acl(
    body: NewAcl, guard: InBodyDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    await guard.check(body.project)
    session = guard.session
    return await _change(
        session,
        store,
        lxd,
        "network-acl.create",
        f"{body.project}/{body.name}",
        "POST",
        "/1.0/network-acls",
        {"project": body.project},
        json=body.model_dump(exclude={"project"}),
    )


@router.put("/network-acls/{project}/{name}")
async def update_acl(
    project: ProjectName,
    name: ObjName,
    body: AclBody,
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "network-acl.update",
        f"{project}/{name}",
        "PUT",
        _acl(name),
        {"project": project},
        json=body.model_dump(),
    )


@router.post("/network-acls/{project}/{name}/rename")
async def rename_acl(
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
        "network-acl.rename",
        f"{project}/{name}",
        "POST",
        _acl(name),
        {"project": project},
        json={"name": body.name},
        detail={"to": body.name},
    )


@router.delete("/network-acls/{project}/{name}")
async def delete_acl(
    project: ProjectName, name: ObjName, session: ProjectManageDep, store: StoreDep, lxd: LXDDep
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "network-acl.delete",
        f"{project}/{name}",
        "DELETE",
        _acl(name),
        {"project": project},
    )


# --- forwards --------------------------------------------------------------------


def _ip(v: str) -> str:
    ipaddress.ip_address(v)  # raises ValueError -> 422
    return v


ListenAddress = Annotated[str, Path(max_length=45)]


class Port(BaseModel):
    protocol: Literal["tcp", "udp"]
    listen_port: str = Field(pattern=r"^[0-9][0-9,-]*$", max_length=200)
    target_address: str
    target_port: str = Field(default="", pattern=r"^([0-9][0-9,-]*)?$", max_length=200)
    description: str = ""

    _addr = field_validator("target_address")(_ip)


class ForwardBody(BaseModel):
    description: str = ""
    config: dict[str, str] = Field(default_factory=dict)  # target_address = the default target
    ports: list[Port] = Field(default_factory=list)


class NewForward(ForwardBody):
    listen_address: str

    _addr = field_validator("listen_address")(_ip)


def _fwd(network: str, listen: str | None = None) -> str:
    base = f"/1.0/networks/{network}/forwards"
    return f"{base}/{listen}" if listen else base


def _listen(v: str) -> str:
    try:
        return _ip(v)
    except ValueError as e:
        raise HTTPException(422, "listen address must be an IP address") from e


def _params(project: str, target: str | None) -> dict[str, Any]:
    return {"project": project, **({"target": target} if target else {})}


@router.get("/networks/{project}/{network}/forwards")
async def list_forwards(
    project: ProjectName, network: ObjName, _: ProjectReadDep, lxd: LXDDep
) -> list[dict[str, Any]]:
    data = await lxd.get(_fwd(network), recursion=1, project=project)
    return list(data or [])


@router.post("/networks/{project}/{network}/forwards")
async def create_forward(
    project: ProjectName,
    network: ObjName,
    body: NewForward,
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "network-forward.create",
        f"{project}/{network}/{body.listen_address}",
        "POST",
        _fwd(network),
        _params(project, target),
        json=body.model_dump(),
    )


@router.put("/networks/{project}/{network}/forwards/{listen}")
async def update_forward(
    project: ProjectName,
    network: ObjName,
    listen: ListenAddress,
    body: ForwardBody,
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> dict[str, Any]:
    _listen(listen)
    return await _change(
        session,
        store,
        lxd,
        "network-forward.update",
        f"{project}/{network}/{listen}",
        "PUT",
        _fwd(network, listen),
        _params(project, target),
        json=body.model_dump(),
    )


@router.delete("/networks/{project}/{network}/forwards/{listen}")
async def delete_forward(
    project: ProjectName,
    network: ObjName,
    listen: ListenAddress,
    session: ProjectManageDep,
    store: StoreDep,
    lxd: LXDDep,
    target: MemberName = None,
) -> dict[str, Any]:
    _listen(listen)
    return await _change(
        session,
        store,
        lxd,
        "network-forward.delete",
        f"{project}/{network}/{listen}",
        "DELETE",
        _fwd(network, listen),
        _params(project, target),
    )
