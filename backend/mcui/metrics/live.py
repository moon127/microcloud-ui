"""Live per-instance metrics: the last two scrapes of every member, as rates (MO-1)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from mcui.lxd.client import LXDClient
from mcui.lxd.server import Member, members
from mcui.metrics.prom import Exposition
from mcui.metrics.scrape import MemberScrape, Scraper

IDLE_CPU_MODES = frozenset({"idle", "iowait", "steal"})
MIN_SCRAPE_AGE = 10.0  # LXD caches /1.0/metrics for ~8s (D7)

_GAUGES = {
    "lxd_cpu_effective_total": "cpus",
    "lxd_memory_MemTotal_bytes": "mem_total",
    "lxd_memory_MemAvailable_bytes": "mem_available",
    "lxd_memory_RSS_bytes": "mem_rss",
    "lxd_memory_Cached_bytes": "mem_cached",
    "lxd_memory_Swap_bytes": "mem_swap",
    "lxd_memory_OOM_kills_total": "oom_kills",
    "lxd_procs_total": "procs",
}
_SUMMED = {
    "lxd_disk_read_bytes_total": "disk_read",
    "lxd_disk_written_bytes_total": "disk_written",
    "lxd_disk_reads_completed_total": "disk_reads",
    "lxd_disk_writes_completed_total": "disk_writes",
}
_NET = {
    "lxd_network_receive_bytes_total": "net_rx",
    "lxd_network_transmit_bytes_total": "net_tx",
}
_FS = {
    "lxd_filesystem_size_bytes": "rootfs_size",
    "lxd_filesystem_avail_bytes": "rootfs_avail",
}
_DAEMON = {
    "lxd_uptime_seconds": "uptime_seconds",
    "lxd_go_goroutines": "goroutines",
    "lxd_warnings_total": "warnings",
    "lxd_api_requests_ongoing": "api_requests_ongoing",
    "lxd_operations_total": "operations",
}


@dataclass
class Totals:
    """Raw per-instance values from one scrape."""

    type: str = ""
    values: dict[str, float] = field(default_factory=dict)
    cpu_ids: set[str] = field(default_factory=set)

    def add(self, key: str, v: float) -> None:
        self.values[key] = self.values.get(key, 0.0) + v


InstanceKey = tuple[str, str]  # (project, name)


def instance_totals(exp: Exposition) -> dict[InstanceKey, Totals]:
    out: dict[InstanceKey, Totals] = {}
    for s in exp.samples:
        name = s.labels.get("name")
        project = s.labels.get("project")
        if name is None or project is None:
            continue
        t = out.setdefault((project, name), Totals())
        t.type = s.labels.get("type", t.type)
        if s.name == "lxd_cpu_seconds_total":
            t.cpu_ids.add(s.labels.get("cpu", ""))
            if s.labels.get("mode") not in IDLE_CPU_MODES:
                t.add("cpu_busy", s.value)
        elif s.name in _GAUGES:
            t.add(_GAUGES[s.name], s.value)
        elif s.name in _SUMMED:
            t.add(_SUMMED[s.name], s.value)
        elif s.name in _NET:
            if s.labels.get("device") != "lo":
                t.add(_NET[s.name], s.value)
        elif s.name in _FS and (
            s.labels.get("mountpoint") == "/" or s.labels.get("device") == "root"
        ):
            t.values[_FS[s.name]] = s.value
    return out


def daemon_values(exp: Exposition) -> dict[str, float]:
    out: dict[str, float] = {}
    for s in exp.samples:
        if s.name in _DAEMON and "name" not in s.labels:
            key = _DAEMON[s.name]
            out[key] = out.get(key, 0.0) + s.value
    return out


def counter_rate(prev: float | None, cur: float | None, dt: float) -> float | None:
    """Per-second rate; a decrease is a counter reset, so ``cur`` is the increase."""
    if prev is None or cur is None or dt <= 0:
        return None
    delta = cur - prev if cur >= prev else cur
    return delta / dt


@dataclass
class InstanceLive:
    member: str
    project: str
    name: str
    type: str
    cpu_percent: float | None
    cpus: float | None
    memory_total: float | None
    memory_used: float | None
    memory_rss: float | None
    memory_cached: float | None
    memory_swap: float | None
    oom_kills: float | None
    disk_read_bps: float | None
    disk_write_bps: float | None
    disk_read_iops: float | None
    disk_write_iops: float | None
    net_rx_bps: float | None
    net_tx_bps: float | None
    procs: float | None
    rootfs_size: float | None
    rootfs_used: float | None


def instance_live(
    member: str, key: InstanceKey, cur: Totals, prev: Totals | None, dt: float
) -> InstanceLive:
    c = cur.values
    p = prev.values if prev else {}

    def rate(k: str) -> float | None:
        return counter_rate(p.get(k), c.get(k), dt) if prev else None

    cpu = rate("cpu_busy")
    # VMs report lxd_cpu_effective_total 0; count their vCPUs instead
    cpus = c.get("cpus") or float(len(cur.cpu_ids)) or None
    cpu_pct = cpu / cpus * 100 if cpu is not None and cpus else None
    used = None
    if "mem_total" in c and "mem_available" in c:
        used = c["mem_total"] - c["mem_available"]
    rootfs_used = None
    if "rootfs_size" in c and "rootfs_avail" in c:
        rootfs_used = c["rootfs_size"] - c["rootfs_avail"]
    return InstanceLive(
        member=member,
        project=key[0],
        name=key[1],
        type=cur.type,
        cpu_percent=cpu_pct,
        cpus=cpus,
        memory_total=c.get("mem_total"),
        memory_used=used,
        memory_rss=c.get("mem_rss"),
        memory_cached=c.get("mem_cached"),
        memory_swap=c.get("mem_swap"),
        oom_kills=c.get("oom_kills"),
        disk_read_bps=rate("disk_read"),
        disk_write_bps=rate("disk_written"),
        disk_read_iops=rate("disk_reads"),
        disk_write_iops=rate("disk_writes"),
        net_rx_bps=rate("net_rx"),
        net_tx_bps=rate("net_tx"),
        procs=c.get("procs"),
        rootfs_size=c.get("rootfs_size"),
        rootfs_used=rootfs_used,
    )


@dataclass
class _MemberState:
    member: Member
    scrape: MemberScrape
    cur: dict[InstanceKey, Totals] | None = None
    cur_at: float = 0.0
    prev: dict[InstanceKey, Totals] | None = None
    prev_at: float = 0.0


class LiveMetrics:
    """Scrapes on demand, at most every ``min_age`` seconds, keeping two samples."""

    def __init__(
        self,
        api: LXDClient,
        scraper: Scraper,
        *,
        min_age: float = MIN_SCRAPE_AGE,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.api = api
        self.scraper = scraper
        self.min_age = min_age
        self.clock = clock or scraper.clock
        self._state: dict[str, _MemberState] = {}
        self._last: float | None = None
        self._lock = asyncio.Lock()

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            if self._last is None or self.clock() - self._last >= self.min_age:
                await self.refresh()
        return self._build()

    async def refresh(self) -> None:
        ms = await members(self.api)
        scrapes = await self.scraper.scrape_all(ms)
        new: dict[str, _MemberState] = {}
        for m, s in zip(ms, scrapes, strict=True):
            old = self._state.get(m.name)
            st = _MemberState(member=m, scrape=s)
            if s.ok:
                st.cur, st.cur_at = instance_totals(s.exposition), s.at
                if old is not None and old.cur is not None:
                    st.prev, st.prev_at = old.cur, old.cur_at
            new[m.name] = st
        self._state = new
        self._last = self.clock()

    def _build(self) -> dict[str, Any]:
        out_members: list[dict[str, Any]] = []
        instances: list[dict[str, Any]] = []
        for st in self._state.values():
            daemon = daemon_values(st.scrape.exposition) if st.scrape.ok else {}
            cur = st.cur or {}
            out_members.append(
                {
                    **st.member.as_dict(),
                    "scrape": st.scrape.status(),
                    "daemon": daemon,
                    "instances": len(cur),
                }
            )
            dt = st.cur_at - st.prev_at
            for key, totals in sorted(cur.items()):
                prev = st.prev.get(key) if st.prev else None
                instances.append(asdict(instance_live(st.member.name, key, totals, prev, dt)))
        return {
            "scraped_at": self._last,
            "interval_seconds": self.scraper.settings.interval_seconds,
            "members": out_members,
            "instances": instances,
        }
