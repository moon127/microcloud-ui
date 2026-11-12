"""OVN network load balancers and their pools (NW-4/7; Phase 5b).

A load balancer listens on an address from the uplink (``0.0.0.0``/``::`` asks LXD to
allocate one, ``network_allocate_external_ips``). Each port sends to named
``backends`` (any LXD with load balancers) or to a ``target_pool`` (LXD 6.9+,
``network_load_balancer_pool``): a pool lists instances and a target port, and with
LXD 6.10's ``network_load_balancer_pool_health_checks`` it can probe them, its state
showing each target online or offline. Every change: admin + CSRF, audited.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator

from mcui.api.acls import ListenAddress, _change, _ip, _listen
from mcui.api.deps import AdminDep, LXDDep, SessionDep, StoreDep
from mcui.api.instances import NAME, ProjectName
from mcui.api.manage import ObjName

router = APIRouter(prefix="/api/networks")

PORTS = r"^[0-9][0-9,-]*$"


class Backend(BaseModel):
    name: str = Field(pattern=NAME, max_length=63)
    target_address: str
    target_port: str = Field(default="", pattern=r"^([0-9][0-9,-]*)?$", max_length=200)
    description: str = ""

    _addr = field_validator("target_address")(_ip)


class LbPort(BaseModel):
    protocol: Literal["tcp", "udp"]
    listen_port: str = Field(pattern=PORTS, max_length=200)
    target_backend: list[str] = Field(default_factory=list)
    target_pool: str = Field(default="", pattern=r"^([A-Za-z0-9][A-Za-z0-9._-]*)?$")
    description: str = ""

    def lxd(self) -> dict[str, Any]:
        out = self.model_dump(exclude={"target_backend", "target_pool"})
        # LXD wants exactly one of them (it says so: "Missing target_backend or target_pool")
        if self.target_pool:
            out["target_pool"] = self.target_pool
        else:
            out["target_backend"] = self.target_backend
        return out


class LbBody(BaseModel):
    description: str = ""
    config: dict[str, str] = Field(default_factory=dict)
    backends: list[Backend] = Field(default_factory=list)
    ports: list[LbPort] = Field(default_factory=list)

    def lxd(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "config": self.config,
            "backends": [b.model_dump() for b in self.backends],
            "ports": [p.lxd() for p in self.ports],
        }


class NewLb(LbBody):
    # an address, or "0.0.0.0" / "::" to have LXD allocate one from the uplink
    listen_address: str

    _addr = field_validator("listen_address")(_ip)


class PoolBody(BaseModel):
    description: str = ""
    protocol: Literal["tcp", "udp"] = "tcp"
    target_port: str = Field(pattern=PORTS, max_length=200)
    instances: list[str] = Field(default_factory=list)
    # LXD 6.10+ (network_load_balancer_pool_health_checks)
    healthcheck: bool = False
    healthcheck_interval: int | None = Field(default=None, ge=1, le=3600)

    def lxd(self) -> dict[str, Any]:
        config = {"protocol": self.protocol, "target_port": self.target_port}
        if self.healthcheck:
            config["healthcheck"] = "true"
            if self.healthcheck_interval:
                config["healthcheck.interval"] = str(self.healthcheck_interval)
        return {
            "description": self.description,
            "config": config,
            "instances": [{"name": n} for n in self.instances],
        }


class NewPool(PoolBody):
    name: str = Field(pattern=NAME, max_length=63)


def _lb(network: str, listen: str | None = None) -> str:
    base = f"/1.0/networks/{network}/load-balancers"
    return f"{base}/{listen}" if listen else base


def _pool(network: str, name: str | None = None) -> str:
    base = f"/1.0/networks/{network}/load-balancer-pools"
    return f"{base}/{name}" if name else base


# --- load balancers ------------------------------------------------------------------


@router.get("/{project}/{network}/load-balancers")
async def list_lbs(
    project: ProjectName, network: ObjName, _: SessionDep, lxd: LXDDep
) -> list[dict[str, Any]]:
    return list(await lxd.get(_lb(network), recursion=1, project=project) or [])


@router.post("/{project}/{network}/load-balancers")
async def create_lb(
    project: ProjectName,
    network: ObjName,
    body: NewLb,
    session: AdminDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "network-load-balancer.create",
        f"{project}/{network}/{body.listen_address}",
        "POST",
        _lb(network),
        {"project": project},
        json={"listen_address": body.listen_address, **body.lxd()},
    )


@router.put("/{project}/{network}/load-balancers/{listen}")
async def update_lb(
    project: ProjectName,
    network: ObjName,
    listen: ListenAddress,
    body: LbBody,
    session: AdminDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    _listen(listen)
    return await _change(
        session,
        store,
        lxd,
        "network-load-balancer.update",
        f"{project}/{network}/{listen}",
        "PUT",
        _lb(network, listen),
        {"project": project},
        json=body.lxd(),
    )


@router.delete("/{project}/{network}/load-balancers/{listen}")
async def delete_lb(
    project: ProjectName,
    network: ObjName,
    listen: ListenAddress,
    session: AdminDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    _listen(listen)
    return await _change(
        session,
        store,
        lxd,
        "network-load-balancer.delete",
        f"{project}/{network}/{listen}",
        "DELETE",
        _lb(network, listen),
        {"project": project},
    )


# --- pools (LXD 6.9+) -------------------------------------------------------------------


@router.get("/{project}/{network}/load-balancer-pools")
async def list_pools(
    project: ProjectName, network: ObjName, _: SessionDep, lxd: LXDDep
) -> list[dict[str, Any]]:
    return list(await lxd.get(_pool(network), recursion=1, project=project) or [])


@router.get("/{project}/{network}/load-balancer-pools/{name}/state")
async def pool_state(
    project: ProjectName, network: ObjName, name: ObjName, _: SessionDep, lxd: LXDDep
) -> dict[str, Any]:
    """Each target's health: ``online``, ``offline`` or ``pending`` (health checks)."""
    data: dict[str, Any] = await lxd.get(f"{_pool(network, name)}/state", project=project)
    return data


@router.post("/{project}/{network}/load-balancer-pools")
async def create_pool(
    project: ProjectName,
    network: ObjName,
    body: NewPool,
    session: AdminDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "network-load-balancer-pool.create",
        f"{project}/{network}/{body.name}",
        "POST",
        _pool(network),
        {"project": project},
        json={"name": body.name, **body.lxd()},
    )


@router.put("/{project}/{network}/load-balancer-pools/{name}")
async def update_pool(
    project: ProjectName,
    network: ObjName,
    name: ObjName,
    body: PoolBody,
    session: AdminDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "network-load-balancer-pool.update",
        f"{project}/{network}/{name}",
        "PUT",
        _pool(network, name),
        {"project": project},
        json=body.lxd(),
    )


@router.delete("/{project}/{network}/load-balancer-pools/{name}")
async def delete_pool(
    project: ProjectName,
    network: ObjName,
    name: ObjName,
    session: AdminDep,
    store: StoreDep,
    lxd: LXDDep,
) -> dict[str, Any]:
    return await _change(
        session,
        store,
        lxd,
        "network-load-balancer-pool.delete",
        f"{project}/{network}/{name}",
        "DELETE",
        _pool(network, name),
        {"project": project},
    )
