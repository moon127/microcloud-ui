"""Hosts at a glance (MO-4, MO-12, D58): per member, what LXD reports (load, coarse
memory, instances and what they've been given) plus node_exporter's view when it's
installed (CPU use by mode, memory with cache, swap, root filesystem, disks, pressure).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException

from mcui.api.deps import ServicesDep, SessionDep
from mcui.lxd.client import LXDClient, LXDError
from mcui.lxd.server import Member, members
from mcui.metrics.store import MetricStore

router = APIRouter(prefix="/api")

_UNITS = {
    "": 1,
    "b": 1,
    "kb": 1000,
    "mb": 1000**2,
    "gb": 1000**3,
    "tb": 1000**4,
    "pb": 1000**5,
    "kib": 1024,
    "mib": 1024**2,
    "gib": 1024**3,
    "tib": 1024**4,
    "pib": 1024**5,
}
_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]*)\s*$")
# what LXD gives a VM that sets no limits; containers are unlimited
VM_DEFAULT_CPUS = 1
VM_DEFAULT_MEMORY = 1024**3


def parse_size(v: str) -> float | None:
    """``2GiB``, ``512MB``, ``1073741824`` -> bytes; ``None`` if not a size."""
    m = _SIZE.match(v)
    if not m or m.group(2).lower() not in _UNITS:
        return None
    return float(m.group(1)) * _UNITS[m.group(2).lower()]


def cpu_count(v: str) -> int | None:
    """``limits.cpu``: a count (``4``) or a pinned set (``0-3,8``)."""
    v = v.strip()
    if v.isdigit():
        return int(v)
    n = 0
    for part in v.split(","):
        lo, sep, hi = part.strip().partition("-")
        if not lo.isdigit() or (sep and not hi.isdigit()):
            return None
        n += int(hi) - int(lo) + 1 if sep else 1
    return n or None


def allocation(inst: dict[str, Any], host_memory: float | None) -> tuple[int | None, float | None]:
    """vCPUs and memory an instance is limited to; ``None`` means unlimited."""
    cfg = inst.get("expanded_config") or inst.get("config") or {}
    vm = inst.get("type") == "virtual-machine"
    cpus = cpu_count(cfg["limits.cpu"]) if cfg.get("limits.cpu") else None
    mem_s = cfg.get("limits.memory", "")
    mem: float | None = None
    if mem_s.endswith("%"):
        with contextlib.suppress(ValueError):
            mem = float(mem_s[:-1]) / 100 * host_memory if host_memory else None
    elif mem_s:
        mem = parse_size(mem_s)
    if vm:
        cpus = cpus or VM_DEFAULT_CPUS
        mem = mem or VM_DEFAULT_MEMORY
    return cpus, mem


async def _state(lxd: LXDClient, m: Member) -> dict[str, Any]:
    try:
        return await lxd.get(f"/1.0/cluster/members/{m.name}/state") or {}
    except LXDError:  # standalone (404) or member down: no sysinfo
        return {}


async def _last_rates(store: MetricStore | None, name: str) -> dict[str, float]:
    """Latest rate per member of a host network counter, summed over interfaces."""
    if store is None:
        return {}
    end = int(time.time())
    res = await store.query(name, None, end - 600, end, fn="rate", by=["member"])
    return {s.labels["member"]: s.points[-1][1] for s in res.series if s.points}


@router.get("/hosts")
async def hosts(_: SessionDep, services: ServicesDep) -> dict[str, Any]:
    lxd = services.lxd
    if lxd is None:
        raise HTTPException(status_code=503, detail="LXD is not configured")
    ms = await members(lxd)
    states, insts, live, rx, tx = await asyncio.gather(
        asyncio.gather(*(_state(lxd, m) for m in ms)),
        lxd.get("/1.0/instances", recursion=1, **{"all-projects": "true"}),
        services.live.snapshot() if services.live else asyncio.sleep(0, {"instances": []}),
        _last_rates(services.metrics, "mcui_host_network_receive_bytes_total"),
        _last_rates(services.metrics, "mcui_host_network_transmit_bytes_total"),
    )
    node = services.node
    collecting = services.collector is not None and services.collector.running
    if node is not None and not collecting:
        await node.scrape_all(ms)
    single = len(ms) == 1
    out = []
    for m, state in zip(ms, states, strict=True):
        si = state.get("sysinfo") or {}
        mem_total = si.get("total_ram")
        mine = [i for i in insts or [] if single or i.get("location") == m.name]
        running = [i for i in mine if i.get("status") == "Running"]
        cpus_alloc, mem_alloc, unlimited = 0, 0.0, 0
        for i in running:
            c, mem = allocation(i, mem_total)
            cpus_alloc += c or 0
            mem_alloc += mem or 0
            unlimited += c is None or mem is None
        cores = mem_used = 0.0
        for i in live.get("instances", []):
            if i["member"] != m.name:
                continue
            if i.get("cpu_percent") is not None and i.get("cpus"):
                cores += i["cpu_percent"] * i["cpus"] / 100
            mem_used += i.get("memory_used") or 0
        load = si.get("load_averages") or []
        out.append(
            {
                "name": m.name,
                "status": m.status,
                "roles": m.roles,
                "cpus": si.get("logical_cpus"),
                "load1": load[0] if load else None,
                "load5": load[1] if len(load) > 1 else None,
                "uptime": si.get("uptime"),
                "memory_total": mem_total,
                "memory_free": si.get("free_ram"),
                "memory_buffered": si.get("buffered_ram"),
                "swap_total": si.get("total_swap"),
                "swap_free": si.get("free_swap"),
                "instances": len(mine),
                "instances_running": len(running),
                "instances_cpu": cores,
                "instances_memory": mem_used,
                "allocated_cpus": cpus_alloc,
                "allocated_memory": mem_alloc,
                "unlimited": unlimited,
                "net_rx_bps": rx.get(m.name),
                "net_tx_bps": tx.get(m.name),
                "node": node.now(m.name) if node is not None else None,
            }
        )
    return {"node_exporter": node is not None, "hosts": out}
