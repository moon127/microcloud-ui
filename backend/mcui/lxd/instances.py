"""Instance index: summaries plus which guest interface sits on which network (MO-8)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from mcui.lxd.client import LXDClient

CACHE_SECONDS = 10.0  # short: lists must reflect changes made outside the UI


@dataclass
class InstanceInfo:
    project: str
    name: str
    type: str
    status: str
    location: str
    description: str
    profiles: list[str] = field(default_factory=list)
    # guest interface name (as in lxd_network_* metrics) -> LXD network
    nics: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "name": self.name,
            "type": self.type,
            "status": self.status,
            "location": self.location,
            "description": self.description,
            "profiles": self.profiles,
            "nics": self.nics,
        }


def guest_nics(inst: dict[str, Any]) -> dict[str, str]:
    """Map guest interfaces to networks via MAC address.

    VM guests name interfaces themselves (``enp5s0``), so the LXD device name
    (``eth0``) can't be used; ``volatile.<dev>.hwaddr`` matches the guest's MAC.
    """
    config = inst.get("expanded_config") or {}
    by_mac: dict[str, str] = {}
    for dev, d in (inst.get("expanded_devices") or {}).items():
        if d.get("type") != "nic":
            continue
        net = d.get("network") or d.get("parent")
        mac = d.get("hwaddr") or config.get(f"volatile.{dev}.hwaddr")
        if net and mac:
            by_mac[str(mac).lower()] = str(net)
    out = {}
    for iface, st in (((inst.get("state") or {}).get("network")) or {}).items():
        net = by_mac.get(str(st.get("hwaddr") or "").lower())
        if net:
            out[iface] = net
    return out


def summarize(inst: dict[str, Any]) -> InstanceInfo:
    return InstanceInfo(
        project=inst.get("project", "default"),
        name=inst["name"],
        type=inst.get("type", ""),
        status=inst.get("status", ""),
        location=inst.get("location", ""),
        description=inst.get("description", ""),
        profiles=list(inst.get("profiles") or []),
        nics=guest_nics(inst),
    )


class InstanceIndex:
    """All instances across projects, refreshed at most every ``CACHE_SECONDS``."""

    def __init__(
        self,
        api: LXDClient,
        *,
        max_age: float = CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.api = api
        self.max_age = max_age
        self.clock = clock
        self._items: list[InstanceInfo] | None = None
        self._at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> list[InstanceInfo]:
        async with self._lock:
            if self._items is None or self.clock() - self._at >= self.max_age:
                data = await self.api.get("/1.0/instances", recursion=2, **{"all-projects": "true"})
                self._items = sorted(
                    (summarize(i) for i in data), key=lambda i: (i.project, i.name)
                )
                self._at = self.clock()
            return self._items

    def invalidate(self) -> None:
        """Forget the cached list (after a change made through the UI)."""
        self._items = None

    async def nic_networks(self) -> dict[tuple[str, str, str], str]:
        return {
            (i.project, i.name, iface): net
            for i in await self.get()
            for iface, net in i.nics.items()
        }
