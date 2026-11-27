"""What's wrong now (D91). Each rule looks at data the app already collects and returns
the alerts it finds; raising ``NoData`` means it couldn't look this round (the engine then
leaves its alerts as they are). Threshold rules clear ``MARGIN`` points below their
threshold once their alert is open, so a value hovering at the line doesn't flap.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import ssl
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from mcui.alerts.store import Alert, Severity
from mcui.config import DEFAULT_THRESHOLDS
from mcui.lxd.client import LXDError
from mcui.lxd.server import Member, members, release
from mcui.metrics.live import SKIP_FSTYPES
from mcui.metrics.prom import Exposition
from mcui.metrics.select import NODE_SKIP_MOUNT_PREFIXES

if TYPE_CHECKING:
    from mcui.services import Services

log = logging.getLogger(__name__)
BROKEN: set[str] = set()  # rules whose failure was logged, until they work again
MARGIN = 2.0  # percentage points (or days) below a threshold before an open alert clears
# members that are running (evacuated ones still are)
ONLINE = frozenset({"Online", "Evacuated", ""})
REMOTE_DRIVERS = frozenset({"ceph", "cephfs", "cephobject", "powerflex", "pure", "alletra"})

# short descriptions of Ceph's common health checks; others say "Ceph reports NAME"
CEPH_CHECKS: dict[str, str] = {
    "OSD_NEARFULL": "An OSD is nearly full",
    "OSD_BACKFILLFULL": "An OSD is too full to take backfill",
    "OSD_FULL": "An OSD is full: writes to its pools stop",
    "POOL_NEARFULL": "A Ceph pool is nearly full",
    "POOL_FULL": "A Ceph pool is full",
    "OSD_DOWN": "An OSD is down",
    "OSD_HOST_DOWN": "All OSDs on a host are down",
    "MON_DOWN": "A Ceph monitor is down",
    "MON_DISK_LOW": "A Ceph monitor's disk is low on space",
    "MON_DISK_CRIT": "A Ceph monitor's disk is critically low on space",
    "MON_CLOCK_SKEW": "Ceph monitors' clocks disagree",
    "MGR_DOWN": "No Ceph manager is running",
    "PG_DEGRADED": "Some data has fewer copies than it should",
    "PG_AVAILABILITY": "Some data is unavailable",
    "SLOW_OPS": "Ceph requests are slow",
    "OSDMAP_FLAGS": "Ceph cluster flags are set (e.g. noout)",
    "RECENT_CRASH": "A Ceph daemon crashed recently",
    "POOL_NO_REDUNDANCY": "A Ceph pool keeps only one copy",
}


class NoData(Exception):
    """The rule couldn't look this round (data missing or stale)."""


@dataclass
class Context:
    services: Services
    open: dict[str, str]  # open alerts' keys and severities, for hysteresis
    thresholds: dict[str, tuple[float, float]]
    now: float = field(default_factory=time.time)
    # key prefixes a rule couldn't see this round (one member's exporter failing): their
    # open alerts stay as they are (D91 review)
    unknown: set[str] = field(default_factory=set)
    _cache: dict[str, Any] = field(default_factory=dict)

    async def once(self, name: str, load: Callable[[], Awaitable[Any]]) -> Any:
        """``load()`` once per round: listings several rules read (D91 review)."""
        if name not in self._cache:
            self._cache[name] = asyncio.ensure_future(load())
        return await asyncio.shield(self._cache[name])

    def level(
        self, rule: str, key: str, value: float, *, lower_is_worse: bool = False
    ) -> Severity | None:
        """The severity ``value`` reaches against the rule's thresholds, if any. The
        margin only delays clearing or stepping down: a warning turns critical at the
        critical threshold itself (D91 review)."""
        warn, crit = self.thresholds.get(rule, DEFAULT_THRESHOLDS[rule])
        now = self.open.get(key)
        crit_margin = MARGIN if now == "critical" else 0.0
        warn_margin = MARGIN if now else 0.0
        if lower_is_worse:
            if value <= crit + crit_margin:
                return "critical"
            return "warning" if value <= warn + warn_margin else None
        if value >= crit - crit_margin:
            return "critical"
        return "warning" if value >= warn - warn_margin else None


async def _projects(ctx: Context) -> list[dict[str, Any]]:
    lxd = ctx.services.lxd
    assert lxd is not None  # noqa: S101 - callers check
    rows: list[dict[str, Any]] = (
        await ctx.once("projects", lambda: lxd.get("/1.0/projects", recursion=1)) or []
    )
    return rows


async def _members(ctx: Context) -> list[Member]:
    lxd = ctx.services.lxd
    assert lxd is not None  # noqa: S101 - callers check
    found: list[Member] = await ctx.once("members", lambda: members(lxd))
    return found


def _pct(used: float, total: float) -> float:
    return 100.0 * used / total if total else 0.0


def _threshold_alert(
    ctx: Context,
    rule: str,
    subject: str,
    value: float,
    title: str,
    detail: str,
    link: str,
    project: str | None = None,
) -> Alert | None:
    key = f"{rule}:{subject}"
    sev = ctx.level(rule, key, value)
    if sev is None:
        return None
    warn, crit = ctx.thresholds.get(rule, DEFAULT_THRESHOLDS[rule])
    return Alert(
        rule,
        subject,
        sev,
        title,
        f"{detail} ({value:.0f}%; warning at {warn:g}%, critical at {crit:g}%)",
        link,
        project,
        {"value": round(value, 1), "warning": warn, "critical": crit},
    )


# --- Ceph ----------------------------------------------------------------------------


def _ceph(ctx: Context) -> Exposition | None:
    """The latest Ceph mgr scrape; ``None`` when Ceph isn't monitored."""
    col = ctx.services.collector
    if ctx.services.ceph is None or col is None:
        return None
    interval = ctx.services.settings.metrics.interval_seconds
    last = col.ceph_last
    if last is None or not last.ok or col.ceph_at is None or ctx.now - col.ceph_at > 4 * interval:
        raise NoData("no recent Ceph mgr scrape")
    return last.exposition


async def ceph_health(ctx: Context) -> list[Alert]:
    """Ceph's own health checks, as Ceph raises them (OSD_NEARFULL and the rest)."""
    exp = _ceph(ctx)
    if exp is None:
        return []
    out = []
    for s in exp.samples:
        if s.name != "ceph_health_detail" or not s.value:
            continue
        name, sev = s.labels.get("name", "?"), s.labels.get("severity", "")
        out.append(
            Alert(
                "ceph_health",
                name,
                "critical" if sev == "HEALTH_ERR" else "warning",
                CEPH_CHECKS.get(name, f"Ceph reports {name}"),
                f"Ceph health check {name} ({sev}). Details: ceph health detail",
                "/ceph",
            )
        )
    return out


async def ceph_raw(ctx: Context) -> list[Alert]:
    """Raw capacity used across the Ceph cluster, before Ceph's own warnings."""
    exp = _ceph(ctx)
    if exp is None:
        return []
    vals = {s.name: s.value for s in exp.samples}
    total = vals.get("ceph_cluster_total_bytes")
    used = vals.get("ceph_cluster_total_used_raw_bytes")
    if not total or used is None:
        return []
    a = _threshold_alert(
        ctx,
        "ceph_raw",
        "cluster",
        _pct(used, total),
        "Ceph is filling up",
        "Raw Ceph capacity used",
        "/ceph",
    )
    return [a] if a else []


# --- LXD members, pools, hosts ---------------------------------------------------------


async def local_pool(ctx: Context) -> list[Alert]:
    """Local storage pools (ZFS, LVM, btrfs, dir) on each member."""
    col, lxd = ctx.services.collector, ctx.services.lxd
    if col is None or lxd is None:
        return []
    if not col.state_last:
        # a standalone server has no member state: its pools aren't checked (ALERTS.md)
        st = col.status.get("state")
        if st is not None and st.last_ok is not None and not st.last_error:
            return []  # collected, but no member state: a standalone server
        raise NoData("no member state yet")
    pools = await lxd.get("/1.0/storage-pools", recursion=1) or []
    local = {p["name"] for p in pools if p.get("driver") not in REMOTE_DRIVERS}
    stale_after = 3 * ctx.services.settings.metrics.state_interval_seconds
    out = []
    for member, (at, state) in sorted(col.state_last.items()):
        if ctx.now - at > stale_after:  # an offline member's last figures: unknown
            ctx.unknown.add(f"local_pool:{member}/")
            continue
        for pool, d in (state.get("storage_pools") or {}).items():
            space = (d or {}).get("space") or {}
            if pool not in local or not space.get("total"):
                continue
            a = _threshold_alert(
                ctx,
                "local_pool",
                f"{member}/{pool}",
                _pct(space.get("used") or 0, space["total"]),
                f"Storage pool {pool} on {member} is filling up",
                f"Pool {pool} on {member}",
                f"/storage/pools/{pool}",
            )
            if a:
                out.append(a)
    return out


async def _node(ctx: Context, rule: str) -> list[tuple[str, Exposition]]:
    """Members' latest node_exporter pages. One that can't be read is unknown this round,
    so only its alerts stay as they are (D91 review): erroring (TLS, auth), or nothing
    listening while LXD says the member isn't online (found in the VM test: a member
    that's down looks "absent"). Nothing listening on an online member means node_exporter
    went away: its alerts clear rather than stay for ever."""
    node = ctx.services.node
    if node is None:
        return []
    down: set[str] | None = None
    out = []
    for member, scrape in sorted(node.last.items()):
        if scrape.ok:
            out.append((member, scrape.exposition))
            continue
        if scrape.absent:
            if down is None:
                down = set(node.last)  # without LXD's word, keep them all
                if ctx.services.lxd is not None:
                    with contextlib.suppress(LXDError):
                        down = {m.name for m in await _members(ctx) if m.status not in ONLINE}
            if member not in down:
                continue
        ctx.unknown.update({f"{rule}:{member}", f"{rule}:{member}:"})
    return out


async def host_fs(ctx: Context) -> list[Alert]:
    out = []
    for member, exp in await _node(ctx, "host_fs"):
        size: dict[str, float] = {}
        avail: dict[str, float] = {}
        free: dict[str, float] = {}
        for s in exp.samples:
            mp, fstype = s.labels.get("mountpoint", ""), s.labels.get("fstype", "")
            if fstype in SKIP_FSTYPES or mp.startswith(NODE_SKIP_MOUNT_PREFIXES):
                continue
            if s.name == "node_filesystem_size_bytes":
                size[mp] = s.value
            elif s.name == "node_filesystem_avail_bytes":
                avail[mp] = s.value
            elif s.name == "node_filesystem_free_bytes":
                free[mp] = s.value
        for mp, total in sorted(size.items()):
            if mp not in avail or not total:
                continue
            # as df: blocks reserved for root count neither as used nor available
            used = total - free.get(mp, avail[mp])
            a = _threshold_alert(
                ctx,
                "host_fs",
                f"{member}:{mp}",
                _pct(used, used + avail[mp]),
                f"{mp} on {member} is filling up",
                f"Filesystem {mp} on {member}",
                f"/members/{member}",
            )
            if a:
                out.append(a)
    return out


async def host_memory(ctx: Context) -> list[Alert]:
    out = []
    for member, exp in await _node(ctx, "host_memory"):
        vals = {s.name: s.value for s in exp.samples}
        total = vals.get("node_memory_MemTotal_bytes")
        avail = vals.get("node_memory_MemAvailable_bytes")
        if not total or avail is None:
            continue
        a = _threshold_alert(
            ctx,
            "host_memory",
            member,
            _pct(total - avail, total),
            f"{member} is short of memory",
            f"Memory in use on {member}",
            f"/members/{member}",
        )
        if a:
            out.append(a)
    return out


async def host_clock(ctx: Context) -> list[Alert]:
    """Ceph and OVN need synchronised clocks."""
    out = []
    for member, exp in await _node(ctx, "host_clock"):
        sync = next((s.value for s in exp.samples if s.name == "node_timex_sync_status"), None)
        if sync == 0:
            out.append(
                Alert(
                    "host_clock",
                    member,
                    "warning",
                    f"{member}'s clock isn't synchronised",
                    "The kernel reports the clock unsynchronised (check chrony or "
                    "systemd-timesyncd); Ceph and OVN need it",
                    f"/members/{member}",
                )
            )
    return out


async def member_status(ctx: Context) -> list[Alert]:
    lxd = ctx.services.lxd
    if lxd is None:
        return []
    out = []
    for m in await _members(ctx):
        if m.status in ("Online", ""):
            continue
        evacuated = m.status == "Evacuated"
        out.append(
            Alert(
                "member_status",
                m.name,
                "warning" if evacuated else "critical",
                f"{m.name} is {m.status.lower()}",
                "Evacuated: its instances run elsewhere until it's restored"
                if evacuated
                else f"LXD reports the member {m.status}: {m.message}".rstrip(": "),
                f"/members/{m.name}",
            )
        )
    return out


VERSION_TTL = 1800.0  # members' LXD versions change rarely: asked every 30 minutes
_versions: dict[str, tuple[float, str]] = {}


async def lxd_security(ctx: Context) -> list[Alert]:
    """Members on an LXD release that misses a security fix (as ``mcui check``). Versions
    are cached; an unreachable member is unknown, not cleared (D91 review)."""
    lxd = ctx.services.lxd
    if lxd is None:
        return []
    top = await ctx.once("server", lambda: lxd.get("/1.0"))
    if not (top.get("environment") or {}).get("server_clustered"):
        targets: list[str | None] = [None]  # standalone: no target (LXD would refuse one)
    else:
        targets = [m.name for m in await _members(ctx)]
    out = []
    for name in targets:
        label = name or "server"
        hit = _versions.get(label)
        if hit is None or ctx.now - hit[0] > VERSION_TTL:
            try:
                info = await lxd.get("/1.0", target=name) if name else top
            except LXDError:
                ctx.unknown.add(f"lxd_security:{label}")
                continue
            hit = (ctx.now, str((info.get("environment") or {}).get("server_version", "")))
            _versions[label] = hit
        version = hit[1]
        _, warning = release(version)
        if warning:
            out.append(
                Alert(
                    "lxd_security",
                    label,
                    "critical",
                    f"LXD on {name or 'the server'} needs a security update",
                    f"LXD {version} {warning}",
                    f"/members/{name}" if name else "/",
                )
            )
    return out


# --- instances and projects ---------------------------------------------------------------


async def instance_oom(ctx: Context) -> list[Alert]:
    """Instances whose processes were killed for lack of memory in the last 10 minutes."""
    store = ctx.services.metrics
    if store is None:
        return []
    end = int(ctx.now)
    res = await store.query("lxd_memory_OOM_kills_total", None, end - 600, end, fn="rate", res=0)
    out = []
    for s in res.series:
        if any(v and v > 0 for _, v in s.points):
            project, name = s.labels.get("project", "default"), s.labels.get("name", "?")
            out.append(
                Alert(
                    "instance_oom",
                    f"{project}/{name}",
                    "warning",
                    f"{name} ran out of memory",
                    f"Processes in {project}/{name} were killed for lack of memory in the last "
                    "10 minutes: raise limits.memory or find the cause",
                    f"/instances/{project}/{name}",
                    project,
                )
            )
    return out


async def instance_rootfs(ctx: Context) -> list[Alert]:
    store = ctx.services.metrics
    if store is None:
        return []
    end = int(ctx.now)
    window = 4 * ctx.services.settings.metrics.interval_seconds

    async def latest(name: str) -> dict[tuple[str, str], float]:
        res = await store.query(name, {"mountpoint": "/"}, end - window, end, res=0)
        return {
            (s.labels.get("project", "default"), s.labels.get("name", "?")): s.points[-1][1]
            for s in res.series
            if s.points and s.points[-1][1] is not None
        }

    size, avail = await asyncio.gather(
        latest("lxd_filesystem_size_bytes"), latest("lxd_filesystem_avail_bytes")
    )
    out = []
    for (project, name), total in sorted(size.items()):
        if (project, name) not in avail or not total:
            continue
        a = _threshold_alert(
            ctx,
            "instance_rootfs",
            f"{project}/{name}",
            _pct(total - avail[(project, name)], total),
            f"{name}'s root disk is filling up",
            f"Root filesystem of {project}/{name}",
            f"/instances/{project}/{name}",
            project,
        )
        if a:
            out.append(a)
    return out


LIMIT_NAMES = {
    "disk": "disk",
    "memory": "memory",
    "cpu": "CPU",
    "instances": "instances",
    "containers": "containers",
    "virtual-machines": "VMs",
    "processes": "processes",
    "networks": "networks",
}


async def project_limits(ctx: Context) -> list[Alert]:
    """Projects near their ``limits.*``."""
    lxd = ctx.services.lxd
    if lxd is None:
        return []
    out = []
    for p in await _projects(ctx):
        cfg = p.get("config") or {}
        if not any(k.startswith("limits.") for k in cfg):
            continue
        state = await lxd.get(f"/1.0/projects/{p['name']}/state")
        for res, d in ((state or {}).get("resources") or {}).items():
            # LXD capitalises these ({"Limit": 3, "Usage": 0}; seen live on 6.10)
            d = {str(k).lower(): v for k, v in (d or {}).items()}
            limit, usage = d.get("limit", -1), d.get("usage", 0)
            if res not in LIMIT_NAMES or not isinstance(limit, int | float) or limit <= 0:
                continue
            a = _threshold_alert(
                ctx,
                "project_limits",
                f"{p['name']}/{res}",
                _pct(usage, limit),
                f"Project {p['name']} is near its {LIMIT_NAMES[res]} limit",
                f"{LIMIT_NAMES[res].capitalize()} used of limits.{res} in {p['name']}",
                f"/projects/{p['name']}",
                p["name"],
            )
            if a:
                out.append(a)
    return out


# --- networks -------------------------------------------------------------------------------


async def _networks(ctx: Context) -> list[tuple[str, dict[str, Any]]]:
    """Every managed network once, with its project: default's, and those of projects
    with their own networks. LXD errors propagate: the rule then can't look (D91 review)."""
    lxd = ctx.services.lxd
    assert lxd is not None  # noqa: S101 - callers check

    async def load() -> list[tuple[str, dict[str, Any]]]:
        own = [
            p["name"]
            for p in await _projects(ctx)
            if p["name"] == "default" or (p.get("config") or {}).get("features.networks") == "true"
        ]
        lists = await asyncio.gather(
            *(lxd.get("/1.0/networks", recursion=1, project=p) for p in own)
        )
        return [(p, n) for p, nets in zip(own, lists, strict=True) for n in nets or []]

    found: list[tuple[str, dict[str, Any]]] = await ctx.once("networks", load)
    return found


async def ovn_uplink(ctx: Context) -> list[Alert]:
    """OVN router addresses used from each uplink's ``ipv4.ovn.ranges``."""
    if ctx.services.lxd is None:
        return []
    from mcui.api.ovn import in_ranges, range_size

    nets = await _networks(ctx)
    routers = [
        (
            (n.get("config") or {}).get("network", ""),
            (n.get("config") or {}).get("volatile.network.ipv4.address", ""),
        )
        for _, n in nets
        if n.get("type") == "ovn"
    ]
    out = []
    for _, u in nets:
        ranges = (u.get("config") or {}).get("ipv4.ovn.ranges", "")
        if u.get("type") != "physical" or not ranges:
            continue
        size = range_size(ranges)
        used = sum(
            1 for up, addr in routers if up == u["name"] and addr and in_ranges(addr, ranges)
        )
        if not size:
            continue
        a = _threshold_alert(
            ctx,
            "ovn_uplink",
            u["name"],
            _pct(used, size),
            f"Uplink {u['name']} is running out of OVN addresses",
            f"{used} of {size} addresses in ipv4.ovn.ranges used",
            "/ovn",
        )
        if a:
            out.append(a)
    return out


def _addresses(alloc: dict[str, Any]) -> list[ipaddress.IPv4Address]:
    """The IPv4 addresses of one ``/1.0/network-allocations`` entry ("addresses", seen
    on LXD 6.10; possibly several, comma-separated)."""
    out = []
    for part in str(alloc.get("addresses") or alloc.get("address") or "").split(","):
        try:
            ip = ipaddress.ip_interface(part.strip()).ip
        except ValueError:
            continue
        if isinstance(ip, ipaddress.IPv4Address):
            out.append(ip)
    return out


async def network_addresses(ctx: Context) -> list[Alert]:
    """Bridge and OVN networks running out of IPv4 addresses: every address in the
    network's own subnet that LXD has handed out (DHCP leases, static NICs, forwards, load
    balancers), not counting the network's own gateway."""
    lxd = ctx.services.lxd
    if lxd is None:
        return []
    from mcui.lxd.entities import url_project

    nets = await _networks(ctx)
    own = {p for p, _ in nets}
    allocs = await lxd.get("/1.0/network-allocations", **{"all-projects": "true"}) or []
    by_net: dict[tuple[str, str], list[ipaddress.IPv4Address]] = {}
    for a in allocs:
        if a.get("type") == "network":  # the gateway itself: already left out of the room
            continue
        # an allocation names its network but not the network's project: a project with
        # its own networks owns its instances' allocations, the rest share default's
        project = url_project(str(a.get("used_by", "")))
        key = (project if project in own else "default", str(a.get("network", "")))
        by_net.setdefault(key, []).extend(_addresses(a))
    out = []
    for project, n in nets:
        cidr = (n.get("config") or {}).get("ipv4.address", "")
        if n.get("type") not in ("bridge", "ovn") or "/" not in cidr:
            continue
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue
        room = net.num_addresses - 3  # network, broadcast, the gateway itself
        if room <= 0:
            continue
        count = len({ip for ip in by_net.get((project, n["name"]), []) if ip in net})
        a = _threshold_alert(
            ctx,
            "network_addresses",
            f"{project}/{n['name']}",
            _pct(count, room),
            f"Network {n['name']} is running out of addresses",
            f"{count} of {room} IPv4 addresses in {cidr} in use",
            f"/networks/{project}/{n['name']}",
            None if project == "default" else project,
        )
        if a:
            out.append(a)
    return out


# --- the app itself ------------------------------------------------------------------------


def cert_days(path: Path) -> float:
    """Days until the certificate in ``path`` expires."""
    info = ssl._ssl._test_decode_cert(str(path))  # type: ignore[attr-defined]  # noqa: SLF001
    return (ssl.cert_time_to_seconds(info["notAfter"]) - time.time()) / 86400


async def cert_expiry(ctx: Context) -> list[Alert]:
    """The LXD cluster certificate the app pins, its own client certificates and its HTTPS
    certificate."""
    s = ctx.services.settings
    certs: dict[str, Path | None] = {
        "LXD cluster certificate": s.lxd.server_cert if s.lxd else None,
        "the app's LXD client certificate": s.lxd.client_cert if s.lxd else None,
        "the app's metrics certificate": s.metrics.cert,
        "the app's HTTPS certificate": s.server.tls_cert,
    }
    out = []
    for label, path in certs.items():
        if path is None or not path.is_file():
            continue
        try:
            days = cert_days(path)
        except (ssl.SSLError, ValueError, KeyError, OSError):
            continue
        key = f"cert_expiry:{path}"
        sev = ctx.level("cert_days", key, days, lower_is_worse=True)
        if sev:
            out.append(
                Alert(
                    "cert_expiry",
                    str(path),
                    sev,
                    f"{label[0].upper()}{label[1:]} expires soon"
                    if days > 0
                    else f"{label[0].upper()}{label[1:]} has expired",
                    f"{path} expires in {days:.0f} days"
                    if days > 0
                    else f"{path} expired {-days:.0f} days ago",
                    "",
                    None,
                    {"days": round(days, 1)},
                )
            )
    return out


JOB_NAMES = {
    "lxd": "LXD metrics",
    "ceph": "Ceph metrics",
    "node": "host metrics",
    "state": "member state",
}


async def collection(ctx: Context) -> list[Alert]:
    """Collection jobs that keep failing (Ceph mgr or node_exporter unreachable...)."""
    col = ctx.services.collector
    if col is None:
        return []
    m = ctx.services.settings.metrics
    every = {
        "lxd": m.interval_seconds,
        "ceph": m.interval_seconds,
        "node": m.interval_seconds,
        "state": m.state_interval_seconds,
    }
    out = []
    for job, label in JOB_NAMES.items():
        st = col.status.get(job)
        if st is None or not st.last_error:
            continue
        if st.last_ok is not None and ctx.now - st.last_ok < 3 * every[job]:
            continue
        out.append(
            Alert(
                "collection",
                job,
                "warning",
                f"Collecting {label} fails",
                f"{label.capitalize()}: {st.last_error}"[:500],
                "",
            )
        )
    return out


Rule = Callable[[Context], Awaitable[list[Alert]]]

# name -> (rule, seconds it must hold before firing)
RULES: dict[str, tuple[Rule, float]] = {
    "ceph_health": (ceph_health, 0),  # Ceph debounces its own checks
    "ceph_raw": (ceph_raw, 60),
    "local_pool": (local_pool, 60),
    "host_fs": (host_fs, 60),
    "host_memory": (host_memory, 120),
    "host_clock": (host_clock, 120),
    "member_status": (member_status, 60),
    "lxd_security": (lxd_security, 0),
    "instance_oom": (instance_oom, 0),
    "instance_rootfs": (instance_rootfs, 60),
    "project_limits": (project_limits, 60),
    "ovn_uplink": (ovn_uplink, 60),
    "network_addresses": (network_addresses, 60),
    "cert_expiry": (cert_expiry, 0),
    "collection": (collection, 0),
}


async def evaluate(ctx: Context, disabled: set[str]) -> dict[str, list[Alert] | None]:
    """Every enabled rule, at once. A disabled rule returns nothing (its alerts clear)."""

    async def one(name: str, rule: Rule) -> list[Alert] | None:
        try:
            return await rule(ctx)
        except (NoData, LXDError):
            return None
        except Exception as e:  # noqa: BLE001 - one broken rule mustn't stop the round
            if name not in BROKEN:
                log.warning("alert rule %s failed: %s: %s", name, type(e).__name__, e)
            BROKEN.add(name)
            return None

    names = list(RULES)
    found = await asyncio.gather(
        *(one(n, RULES[n][0]) if n not in disabled else _empty() for n in names)
    )
    for n, res in zip(names, found, strict=True):
        if res is not None:
            BROKEN.discard(n)  # logged again if it breaks again
    return dict(zip(names, found, strict=True))


async def _empty() -> list[Alert]:
    return []
