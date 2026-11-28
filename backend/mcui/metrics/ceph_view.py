"""The Ceph dashboard's current state, from the last ceph-mgr scrape (MO-7, MO-7a).

History comes from the metric store; this is the "now" view: health checks,
monitors, OSDs, placement-group states, raw capacity and pools. RBD image names
are mapped back to the LXD instance or volume they belong to (D56).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any

from mcui.metrics.prom import Exposition

if TYPE_CHECKING:
    from mcui.metrics.node import NodeScraper

HEALTH = {0: "HEALTH_OK", 1: "HEALTH_WARN", 2: "HEALTH_ERR"}
# pool-level PG counts worth showing when non-zero (besides total/active/clean)
PG_PREFIX = "ceph_pg_"


def _by(exp: Exposition, name: str, label: str) -> dict[str, float]:
    return {s.labels.get(label, ""): s.value for s in exp.samples if s.name == name}


def _one(exp: Exposition, name: str) -> float | None:
    for s in exp.samples:
        if s.name == name and not s.labels:
            return s.value
    return None


def summarize(exp: Exposition, nodes: Mapping[str, Exposition] | None = None) -> dict[str, Any]:
    """``nodes``: members' latest node_exporter pages, for monitors' free disk space."""
    status = _one(exp, "ceph_health_status")
    checks = [
        {"name": s.labels.get("name", ""), "severity": s.labels.get("severity", "")}
        for s in exp.samples
        if s.name == "ceph_health_detail" and s.value
    ]
    mgrs = [
        {"name": d.removeprefix("mgr."), "active": bool(v)}
        for d, v in sorted(_by(exp, "ceph_mgr_status", "ceph_daemon").items())
    ]
    return {
        "health": {
            "status": HEALTH.get(int(status), "UNKNOWN") if status is not None else "UNKNOWN",
            "checks": checks,
        },
        "version": _version(exp),
        "mons": mons(exp, nodes or {}),
        "mgrs": mgrs,
        "osds": _osds(exp),
        "perf_counters": _perf_counters(exp),
        "full_ratios": _full_ratios(exp),
        "pgs": _pgs(exp),
        "capacity": {
            "total": _one(exp, "ceph_cluster_total_bytes"),
            "used_raw": _one(exp, "ceph_cluster_total_used_raw_bytes"),
        },
        "pools": _pools(exp),
    }


# MicroCeph keeps a monitor's store in MON_DATA/ceph-<name>. Ceph raises MON_DISK_LOW when
# the filesystem holding it has mon_data_avail_warn (30) percent free or less, MON_DISK_CRIT
# at mon_data_avail_crit (5) or less, counting whole percent (an int): Ceph's defaults,
# which the mgr doesn't export (HealthMonitor::check_member_health, D92)
MON_DATA = "/var/snap/microceph/common/data/mon"
MON_AVAIL_WARN, MON_AVAIL_CRIT = 30, 5
# filesystems that can't hold a monitor's data
_NOT_DATA = frozenset({"proc", "sysfs", "nsfs", "devtmpfs", "squashfs"})


def node_pages(node: NodeScraper | None) -> dict[str, Exposition]:
    """Members' latest node_exporter pages that were read, for ``mons``."""
    return {m: s.exposition for m, s in node.last.items() if s.ok} if node else {}


def mons(exp: Exposition, nodes: Mapping[str, Exposition]) -> list[dict[str, Any]]:
    """Each monitor: its host, whether it's in quorum, and the free space on the
    filesystem holding its data, from that host's node_exporter (``disk`` is ``None``
    when the host has none: in a bigger cluster monitors needn't be on LXD members)."""
    meta = {
        s.labels.get("ceph_daemon", ""): s.labels
        for s in exp.samples
        if s.name == "ceph_mon_metadata"
    }
    quorum = _by(exp, "ceph_mon_quorum_status", "ceph_daemon")
    out = []
    for d in sorted(set(meta) | set(quorum)):
        labels = meta.get(d, {})
        name = d.removeprefix("mon.")
        host = labels.get("hostname", "")
        member = _member(host, nodes)
        rank = labels.get("rank", "")
        out.append(
            {
                "name": name,
                "in_quorum": bool(quorum.get(d)),
                "host": host,
                "rank": int(rank) if rank.isdigit() else None,
                "address": labels.get("public_addr", ""),
                "member": member,
                "disk": _mon_disk(nodes[member], f"{MON_DATA}/ceph-{name}") if member else None,
            }
        )
    return out


def _member(host: str, nodes: Mapping[str, Exposition]) -> str | None:
    """The member a Ceph host is. Ceph names the host as ``uname -n`` does, so that's
    tried first (members can be renamed), then the member's name, each in full and then
    without the domain, ignoring case; a name two members share matches neither."""
    if not host:
        return None
    uname = {
        member: s.labels.get("nodename", "")
        for member, exp in nodes.items()
        for s in exp.samples
        if s.name == "node_uname_info"
    }

    def norm(n: str, short: bool) -> str:
        return (n.split(".")[0] if short else n).lower()

    for short in (False, True):
        for names in (uname, {m: m for m in nodes}):
            found = [m for m, n in names.items() if n and norm(n, short) == norm(host, short)]
            if len(found) == 1:
                return found[0]
    return None


def _mon_disk(exp: Exposition, path: str) -> dict[str, Any] | None:
    """The filesystem holding ``path``: the longest mount point above it. Free is counted
    as Ceph does: available to non-root over the filesystem's size, in whole percent."""
    size: dict[str, float] = {}
    avail: dict[str, float] = {}
    for s in exp.samples:
        mp = s.labels.get("mountpoint", "")
        if s.labels.get("fstype", "") in _NOT_DATA:
            continue
        if not (mp == path or path.startswith(mp.rstrip("/") + "/")):
            continue
        if s.name == "node_filesystem_size_bytes":
            size[mp] = s.value
        elif s.name == "node_filesystem_avail_bytes":
            avail[mp] = s.value
    known = [mp for mp in size if mp in avail and size[mp]]
    if not known:
        return None
    mp = max(known, key=len)
    ceph = int(100.0 * avail[mp] / size[mp])  # as Ceph's int avail_percent
    state = "critical" if ceph <= MON_AVAIL_CRIT else "low" if ceph <= MON_AVAIL_WARN else "ok"
    return {
        "mountpoint": mp,
        "size": size[mp],
        "avail": avail[mp],
        "free_percent": ceph,
        "state": state,
    }


# Ceph's defaults (mon_osd_nearfull_ratio, mon_osd_full_ratio), for mgrs that don't export
# the cluster's own: Squid doesn't, Tentacle does
DEFAULT_NEARFULL, DEFAULT_FULL = 0.85, 0.95


def _full_ratios(exp: Exposition) -> dict[str, Any]:
    """When an OSD counts as nearly full and full (Ceph raises OSD_NEARFULL and stops
    writes at OSD_FULL); ``default`` when the mgr doesn't say and Ceph's defaults apply."""
    nearfull = _one(exp, "ceph_osd_nearfull_ratio")
    full = _one(exp, "ceph_osd_full_ratio")
    return {
        "nearfull": round(nearfull, 4) if nearfull is not None else DEFAULT_NEARFULL,
        "full": round(full, 4) if full is not None else DEFAULT_FULL,
        "default": nearfull is None or full is None,
    }


def _version(exp: Exposition) -> str | None:
    for s in exp.samples:
        if s.name == "ceph_osd_metadata" and s.labels.get("ceph_version"):
            m = re.search(r"ceph version (\S+).*?(\w+) \(", s.labels["ceph_version"])
            return f"{m.group(1)} {m.group(2)}" if m else s.labels["ceph_version"]
    return None


_OSD_VALUES = {
    "ceph_osd_up": "up",
    "ceph_osd_in": "in",
    "ceph_osd_weight": "weight",
    "ceph_osd_numpg": "pgs",
    "ceph_osd_stat_bytes": "size",
    "ceph_osd_stat_bytes_used": "used",
    "ceph_osd_apply_latency_ms": "apply_latency_ms",
    "ceph_osd_commit_latency_ms": "commit_latency_ms",
}


def _perf_counters(exp: Exposition) -> bool:
    """False when OSDs are listed but their daemon perf counters aren't: Ceph Tentacle's mgr
    leaves them out by default (``exclude_perf_counters``), and MicroCeph runs no
    ``ceph-exporter`` (Phase 5). OSD used/size and per-op latency need them."""
    names = {s.name for s in exp.samples}
    if "ceph_osd_metadata" not in names:
        return True  # no OSDs listed: nothing missing to explain
    return "ceph_osd_stat_bytes" in names or "ceph_osd_op_r_latency_sum" in names


def _osd_id(daemon: str) -> int | None:
    m = re.fullmatch(r"osd\.(\d+)", daemon)
    return int(m.group(1)) if m else None


def _osds(exp: Exposition) -> list[dict[str, Any]]:
    osds: dict[int, dict[str, Any]] = {}
    for s in exp.samples:
        oid = _osd_id(s.labels.get("ceph_daemon", ""))
        if oid is None:
            continue
        o = osds.setdefault(oid, {"id": oid})
        if s.name == "ceph_osd_metadata":
            o["host"] = s.labels.get("hostname", "")
            o["device_class"] = s.labels.get("device_class", "")
        elif s.name in _OSD_VALUES:
            key = _OSD_VALUES[s.name]
            o[key] = bool(s.value) if key in ("up", "in") else s.value
    return [osds[k] for k in sorted(osds)]


def _pgs(exp: Exposition) -> dict[str, Any]:
    states: dict[str, float] = {}
    for s in exp.samples:
        if s.name.startswith(PG_PREFIX) and "pool_id" in s.labels:
            state = s.name.removeprefix(PG_PREFIX)
            states[state] = states.get(state, 0.0) + s.value
    total = states.pop("total", 0.0)
    return {"total": total, "states": {k: v for k, v in sorted(states.items()) if v}}


_POOL_VALUES = {
    "ceph_pool_stored": "stored",
    "ceph_pool_stored_raw": "stored_raw",
    "ceph_pool_max_avail": "max_avail",
    "ceph_pool_percent_used": "percent_used",
    "ceph_pool_objects": "objects",
    "ceph_pg_total": "pgs",
}


def _pools(exp: Exposition) -> list[dict[str, Any]]:
    pools: dict[str, dict[str, Any]] = {}
    for s in exp.samples:
        pid = s.labels.get("pool_id")
        if pid is None:
            continue
        if s.name == "ceph_pool_metadata":
            p = pools.setdefault(pid, {"id": int(pid)})
            p.update(
                name=s.labels.get("name", ""),
                type=s.labels.get("type", ""),
                description=s.labels.get("description", ""),
            )
        elif s.name in _POOL_VALUES:
            pools.setdefault(pid, {"id": int(pid)})[_POOL_VALUES[s.name]] = s.value
    return sorted(pools.values(), key=lambda p: p["id"])


# --- RBD images -> LXD objects -------------------------------------------------------

_KINDS = {
    "container": "instance",
    "virtual-machine": "instance",
    "custom": "volume",
    "image": "image",
}


def rbd_owner(image: str, projects: Iterable[str]) -> dict[str, Any]:
    """``container_web`` -> instance web; ``custom_lab_data.block`` -> volume data in lab.

    LXD names RBD images ``<type>_<project>_<name>`` (the project is left out for
    ``default``); a VM's disk is the ``.block`` image. Unknown names come back as
    ``{"kind": "other"}``.
    """
    m = re.fullmatch(r"(zombie_)?(container|virtual-machine|custom|image)_(.+?)(\.block)?", image)
    if not m:
        return {"kind": "other"}
    zombie, typ, rest, block = m.groups()
    out: dict[str, Any] = {"kind": _KINDS[typ], "block": bool(block)}
    if zombie:
        out["deleted"] = True
    if typ == "image":
        out["name"] = rest.split("_", 1)[0]
        return out
    project = "default"
    for p in sorted(projects, key=len, reverse=True):
        if p != "default" and rest.startswith(f"{p}_"):
            project, rest = p, rest[len(p) + 1 :]
            break
    out.update(project=project, name=rest, type=typ)
    return out
