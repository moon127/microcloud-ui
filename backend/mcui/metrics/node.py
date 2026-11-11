"""Host metrics from ``node_exporter`` on each member, when installed (MO-12, D58).

LXD reports host load and coarse memory only; node_exporter adds real CPU use by
mode, memory with page cache and swap, filesystems, disk I/O and pressure stall
information. A member without it simply has no node data: that isn't an error.
"""

from __future__ import annotations

import asyncio
import re
import ssl
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import httpx

from mcui.config import NodeExporterSettings
from mcui.lxd.server import Member
from mcui.metrics.live import counted_disk, counter_rate
from mcui.metrics.prom import Exposition, Sample, parse
from mcui.metrics.scrape import with_port

# Mapped Ceph/NBD images, zvols, optical drives and md RAID (stacked on its disks,
# like dm-*) aren't the host's own disk I/O.
_NOT_HOST_DISK = re.compile(r"^(?:rbd|nbd|zd|sr|md)\d+")

IDLE_MODES = frozenset({"idle", "iowait", "steal"})


def host_disk(device: str) -> bool:
    return counted_disk(device) and not _NOT_HOST_DISK.match(device)


def node_url(member: Member, settings: NodeExporterSettings) -> str:
    override = settings.member_overrides.get(member.name)
    if override:
        return override.rstrip("/")
    return with_port(member.url, settings.port).replace("https://", f"{settings.scheme}://", 1)


@dataclass
class NodeScrape:
    member: str
    url: str
    ok: bool
    at: float
    error: str | None = None
    # nothing listening: node_exporter isn't installed (or is firewalled off)
    absent: bool = False
    exposition: Exposition = field(default_factory=Exposition)

    def status(self) -> dict[str, Any]:
        return {"url": self.url, "ok": self.ok, "error": self.error, "absent": self.absent}


def _ssl_context(settings: NodeExporterSettings) -> ssl.SSLContext | bool:
    if settings.scheme == "http":
        return False
    return ssl.create_default_context(cafile=str(settings.ca) if settings.ca else None)


class NodeScraper:
    """Scrapes every member in parallel and keeps the last two scrapes for rates."""

    def __init__(
        self,
        settings: NodeExporterSettings,
        clock: Callable[[], float] = time.time,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self.clock = clock
        password = settings.password()
        auth = (settings.username, password) if settings.username and password else None
        self._http = httpx.AsyncClient(
            verify=_ssl_context(settings),
            timeout=settings.timeout_seconds,
            auth=auth,
            transport=transport,
        )
        self.last: dict[str, NodeScrape] = {}
        self.prev: dict[str, NodeScrape] = {}

    async def scrape_member(self, member: Member) -> NodeScrape:
        url = node_url(member, self.settings)
        ext = {"sni_hostname": self.settings.server_name} if self.settings.server_name else {}
        try:
            r = await self._http.get(url + "/metrics", extensions=ext)
        except httpx.ConnectError as e:
            if "SSL" in str(e) or "certificate" in str(e).lower():
                return NodeScrape(member.name, url, False, self.clock(), f"TLS: {e}")
            return NodeScrape(
                member.name, url, False, self.clock(), "not reachable (not installed?)", True
            )
        except httpx.HTTPError as e:
            msg = f"{type(e).__name__}: {e}".rstrip(": ")
            return NodeScrape(
                member.name, url, False, self.clock(), msg, isinstance(e, httpx.TimeoutException)
            )
        now = self.clock()
        if r.status_code == 401:
            return NodeScrape(member.name, url, False, now, "HTTP 401: check username/password")
        if r.status_code != 200:
            return NodeScrape(member.name, url, False, now, f"HTTP {r.status_code}")
        exp = parse(r.text)
        if not any(s.name.startswith("node_") for s in exp.samples):
            return NodeScrape(member.name, url, False, now, "not a node_exporter")
        return NodeScrape(member.name, url, True, now, exposition=exp)

    async def scrape_all(self, members: list[Member]) -> list[NodeScrape]:
        res = list(await asyncio.gather(*(self.scrape_member(m) for m in members)))
        for s in res:
            old = self.last.get(s.member)
            if s.ok and old is not None and old.ok:
                self.prev[s.member] = old
            elif not s.ok:
                self.prev.pop(s.member, None)
            self.last[s.member] = s
        names = {m.name for m in members}
        for gone in set(self.last) - names:
            self.last.pop(gone)
            self.prev.pop(gone, None)
        return res

    def now(self, member: str) -> dict[str, Any] | None:
        """Current host values for the Hosts table, or ``None`` before any scrape."""
        cur = self.last.get(member)
        if cur is None:
            return None
        out: dict[str, Any] = cur.status()
        if cur.ok:
            prev = self.prev.get(member)
            out.update(
                host_now(
                    cur.exposition,
                    prev.exposition if prev else None,
                    cur.at - (prev.at if prev else cur.at),
                )
            )
        return out

    async def aclose(self) -> None:
        await self._http.aclose()


def _sum(samples: Iterable[Sample], name: str, key: str | None = None) -> dict[str, float]:
    out: dict[str, float] = {}
    for s in samples:
        if s.name == name:
            k = s.labels.get(key, "") if key else ""
            out[k] = out.get(k, 0.0) + s.value
    return out


def _gauge(exp: Exposition, name: str) -> float | None:
    for s in exp.samples:
        if s.name == name:
            return s.value
    return None


def host_now(cur: Exposition, prev: Exposition | None, dt: float) -> dict[str, Any]:
    """CPU by mode, memory, root filesystem, disk and pressure from two scrapes."""
    out: dict[str, Any] = {}

    def g(name: str) -> float | None:
        return _gauge(cur, name)

    total, avail = g("node_memory_MemTotal_bytes"), g("node_memory_MemAvailable_bytes")
    out["memory_total"] = total
    out["memory_used"] = total - avail if total is not None and avail is not None else None
    cache = [g("node_memory_Cached_bytes"), g("node_memory_Buffers_bytes")]
    out["memory_cache"] = sum(v for v in cache if v is not None) if any(cache) else None
    out["zfs_arc"] = g("node_zfs_arc_size")
    st, sf = g("node_memory_SwapTotal_bytes"), g("node_memory_SwapFree_bytes")
    out["swap_total"] = st
    out["swap_used"] = st - sf if st is not None and sf is not None else None
    for s in cur.samples:
        if s.name == "node_filesystem_size_bytes" and s.labels.get("mountpoint") == "/":
            out["rootfs_size"] = s.value
        elif s.name == "node_filesystem_avail_bytes" and s.labels.get("mountpoint") == "/":
            out["rootfs_avail"] = s.value
    if "rootfs_size" in out and "rootfs_avail" in out:
        out["rootfs_used"] = out["rootfs_size"] - out.pop("rootfs_avail")
    out["timex_sync"] = g("node_timex_sync_status")
    out["timex_offset"] = g("node_timex_offset_seconds")
    if prev is None or dt <= 0:
        return out

    def rate(
        name: str, key: str | None = None, keep: Callable[[str], bool] | None = None
    ) -> dict[str, float]:
        c, p = _sum(cur.samples, name, key), _sum(prev.samples, name, key)
        r = {}
        for k, v in c.items():
            if keep is not None and not keep(k):
                continue
            x = counter_rate(p.get(k), v, dt)
            if x is not None:
                r[k] = x
        return r

    modes = rate("node_cpu_seconds_total", "mode")
    all_ = sum(modes.values())
    if all_ > 0:
        pct = {m: v / all_ * 100 for m, v in modes.items()}
        out["cpu_percent"] = 100 - sum(pct.get(m, 0.0) for m in IDLE_MODES)
        out["cpu_iowait_percent"] = pct.get("iowait", 0.0)
        out["cpu_steal_percent"] = pct.get("steal", 0.0)
    busy = rate("node_disk_io_time_seconds_total", "device", host_disk)
    if busy:
        dev = max(busy, key=lambda k: busy[k])
        out["disk_busy_percent"] = min(busy[dev] * 100, 100.0)
        out["disk_busiest"] = dev
    for key, name in (
        ("disk_read_bps", "node_disk_read_bytes_total"),
        ("disk_write_bps", "node_disk_written_bytes_total"),
    ):
        r = rate(name, "device", host_disk)
        if r:
            out[key] = sum(r.values())
    for key, name in (
        ("pressure_cpu", "node_pressure_cpu_waiting_seconds_total"),
        ("pressure_memory", "node_pressure_memory_waiting_seconds_total"),
        ("pressure_io", "node_pressure_io_waiting_seconds_total"),
    ):
        r = rate(name)
        if r:
            out[key] = r[""] * 100
    return out
