"""OVN overview from the LXD API (MO-9): every OVN network with its uplink, router
addresses, active gateway chassis, forwards, load balancers, peers, ACLs and
allocations, plus how much of each uplink's OVN router range is in use (D57).
"""

from __future__ import annotations

import asyncio
import ipaddress
from collections import Counter
from typing import Any

from fastapi import APIRouter

from mcui.api.deps import LXDDep, SessionDep
from mcui.lxd.client import LXDClient, LXDError

router = APIRouter(prefix="/api")


async def _try(coro: Any) -> Any:
    try:
        return await coro
    except LXDError as e:
        return {"error": str(e)}


def range_size(ranges: str) -> int:
    """Addresses in ``a-b,c-d`` (LXD's ``ipv4.ovn.ranges``); 0 if unparseable."""
    total = 0
    for part in ranges.split(","):
        lo, sep, hi = part.strip().partition("-")
        try:
            a = ipaddress.ip_address(lo)
            b = ipaddress.ip_address(hi) if sep else a
        except ValueError:
            return 0
        total += int(b) - int(a) + 1
    return total


def in_ranges(addr: str, ranges: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("/")[0])
        for part in ranges.split(","):
            lo, sep, hi = part.strip().partition("-")
            a = ipaddress.ip_address(lo)
            b = ipaddress.ip_address(hi) if sep else a
            if a.version == ip.version and int(a) <= int(ip) <= int(b):
                return True
    except ValueError:
        return False
    return False


async def _network(lxd: LXDClient, project: str, n: dict[str, Any]) -> dict[str, Any]:
    name = n["name"]
    base = f"/1.0/networks/{name}"
    state, forwards, lbs, peers = await asyncio.gather(
        _try(lxd.get(f"{base}/state", project=project)),
        _try(lxd.get(f"{base}/forwards", recursion=1, project=project)),
        _try(lxd.get(f"{base}/load-balancers", recursion=1, project=project)),
        _try(lxd.get(f"{base}/peers", recursion=1, project=project)),
    )
    cfg = n.get("config") or {}

    def items(x: Any) -> list[dict[str, Any]] | None:
        return list(x or []) if not isinstance(x, dict) else None

    return {
        "name": name,
        "project": project,
        "description": n.get("description", ""),
        "status": n.get("status", ""),
        "uplink": cfg.get("network", ""),
        "ipv4": cfg.get("ipv4.address", ""),
        "ipv6": cfg.get("ipv6.address", ""),
        "ipv4_nat": cfg.get("ipv4.nat") == "true",
        "ipv6_nat": cfg.get("ipv6.nat") == "true",
        "router_ipv4": cfg.get("volatile.network.ipv4.address", ""),
        "router_ipv6": cfg.get("volatile.network.ipv6.address", ""),
        "acls": [a for a in cfg.get("security.acls", "").split(",") if a],
        "used_by": len(n.get("used_by") or []),
        "chassis": ((state.get("ovn") or {}).get("chassis") if "error" not in state else None),
        "state_error": state.get("error"),
        "forwards": items(forwards),
        "load_balancers": items(lbs),
        "peers": items(peers),
    }


@router.get("/ovn")
async def ovn_overview(_: SessionDep, lxd: LXDDep) -> dict[str, Any]:
    # projects without features.networks use default's networks: list each network once
    projects = [
        p["name"]
        for p in await lxd.get("/1.0/projects", recursion=1) or []
        if p["name"] == "default" or (p.get("config") or {}).get("features.networks") == "true"
    ]
    lists = await asyncio.gather(
        *(_try(lxd.get("/1.0/networks", recursion=1, project=p)) for p in projects)
    )
    ovn: list[tuple[str, dict[str, Any]]] = []
    uplinks: dict[str, dict[str, Any]] = {}
    for p, nets in zip(projects, lists, strict=True):
        if isinstance(nets, dict):
            continue
        for n in nets or []:
            if n.get("type") == "ovn":
                ovn.append((p, n))
            elif n.get("type") == "physical" and n.get("managed"):
                uplinks[n["name"]] = n
    networks = list(await asyncio.gather(*(_network(lxd, p, n) for p, n in ovn)))
    alloc = await _try(lxd.get("/1.0/network-allocations", **{"all-projects": "true"}))
    counts: dict[str, Counter[str]] = {}
    if not isinstance(alloc, dict):
        for a in alloc or []:
            counts.setdefault(a.get("network", ""), Counter())[a.get("type", "")] += 1
    for net in networks:
        net["allocations"] = dict(counts.get(net["name"], {}))
    return {"networks": networks, "uplinks": [_uplink(u, networks) for u in uplinks.values()]}


def _uplink(u: dict[str, Any], networks: list[dict[str, Any]]) -> dict[str, Any]:
    cfg = u.get("config") or {}
    ranges4 = cfg.get("ipv4.ovn.ranges", "")
    ranges6 = cfg.get("ipv6.ovn.ranges", "")
    users = [n for n in networks if n["uplink"] == u["name"]]
    return {
        "name": u["name"],
        "status": u.get("status", ""),
        "ipv4_gateway": cfg.get("ipv4.gateway", ""),
        "ipv6_gateway": cfg.get("ipv6.gateway", ""),
        "ipv4_ranges": ranges4,
        "ipv6_ranges": ranges6,
        "ipv4_routes": cfg.get("ipv4.routes", ""),
        "dns": cfg.get("dns.nameservers", ""),
        "bgp_peers": sorted(
            {k.split(".")[2] for k in cfg if k.startswith("bgp.peers.") and k.count(".") >= 3}
        ),
        "networks": [n["name"] for n in users],
        # router addresses taken from the OVN range (forwards/LBs use routes, not ranges)
        "ipv4_used": sum(
            1 for n in users if n["router_ipv4"] and in_ranges(n["router_ipv4"], ranges4)
        ),
        "ipv4_size": range_size(ranges4) if ranges4 else 0,
    }
