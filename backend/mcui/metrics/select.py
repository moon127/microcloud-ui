"""Which series are kept in history (D34) and the derived series we add.

Only what the graphs use is stored: no Go runtime internals, no per-CPU/per-mode
CPU counters (replaced by one derived busy-seconds counter per instance), no
tmpfs/overlay mounts or container veths inside instances, and no Ceph
BlueStore/BlueFS/RocksDB/MDS/Paxos internals.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from mcui.metrics.live import (
    SKIP_FSTYPES,
    effective_cpus,
    instance_totals,
    memory_used,
    stored_disk,
)
from mcui.metrics.node import host_disk
from mcui.metrics.prom import Exposition
from mcui.metrics.store import Kind, Sample

DROP_LABELS = frozenset({"state"})  # changes on stop/start; would split series

INSTANCE: dict[str, Kind] = {
    "lxd_memory_MemTotal_bytes": "gauge",
    "lxd_memory_MemAvailable_bytes": "gauge",
    "lxd_memory_RSS_bytes": "gauge",
    "lxd_memory_Cached_bytes": "gauge",
    "lxd_memory_Swap_bytes": "gauge",
    "lxd_memory_OOM_kills_total": "counter",
    "lxd_procs_total": "gauge",
    "lxd_disk_read_bytes_total": "counter",
    "lxd_disk_written_bytes_total": "counter",
    "lxd_disk_reads_completed_total": "counter",
    "lxd_disk_writes_completed_total": "counter",
    "lxd_network_receive_bytes_total": "counter",
    "lxd_network_transmit_bytes_total": "counter",
    "lxd_network_receive_packets_total": "counter",
    "lxd_network_transmit_packets_total": "counter",
    "lxd_network_receive_errs_total": "counter",
    "lxd_network_transmit_errs_total": "counter",
    "lxd_network_receive_drop_total": "counter",
    "lxd_network_transmit_drop_total": "counter",
    "lxd_filesystem_size_bytes": "gauge",
    "lxd_filesystem_avail_bytes": "gauge",
}
DAEMON: dict[str, Kind] = {
    "lxd_api_requests_completed_total": "counter",
    "lxd_api_requests_ongoing": "gauge",
    "lxd_operations_total": "gauge",
    "lxd_warnings_total": "gauge",
    "lxd_uptime_seconds": "gauge",
    "lxd_go_goroutines": "gauge",
}
SKIP_MOUNT_PREFIXES = (
    "/run",
    "/dev",
    "/sys",
    "/proc",
    "/snap",
    "/var/lib/containers",
    "/var/lib/docker",
)
# ...unless it's a block device: a bind mount of the root disk there (Podman) (D62)
KEEP_BLOCK_UNDER = ("/var/lib/containers", "/var/lib/docker")
SKIP_NIC_PREFIXES = ("lo", "veth", "docker", "br-", "cni", "flannel", "podman", "virbr")

PG_STATES = [
    "total",
    "active",
    "clean",
    "degraded",
    "undersized",
    "down",
    "stale",
    "inconsistent",
    "incomplete",
    "peering",
    "recovering",
    "backfilling",
    "remapped",
    "scrubbing",
    "deep",
    "unknown",
]
CEPH: frozenset[str] = frozenset(
    {
        "ceph_health_status",
        "ceph_health_detail",
        "ceph_mon_quorum_status",
        "ceph_mon_metadata",
        "ceph_mgr_status",
        "ceph_mgr_metadata",
        "ceph_cluster_total_bytes",
        "ceph_cluster_total_used_bytes",
        "ceph_cluster_total_used_raw_bytes",
        "ceph_osd_metadata",
        "ceph_osd_up",
        "ceph_osd_in",
        "ceph_osd_weight",
        "ceph_osd_numpg",
        "ceph_osd_stat_bytes",
        "ceph_osd_stat_bytes_used",
        "ceph_osd_apply_latency_ms",
        "ceph_osd_commit_latency_ms",
        "ceph_osd_op_r",
        "ceph_osd_op_w",
        "ceph_osd_op_r_out_bytes",
        "ceph_osd_op_w_in_bytes",
        "ceph_osd_op_r_latency_sum",
        "ceph_osd_op_r_latency_count",
        "ceph_osd_op_w_latency_sum",
        "ceph_osd_op_w_latency_count",
        "ceph_pool_metadata",
        "ceph_pool_rd",
        "ceph_pool_wr",
        "ceph_pool_rd_bytes",
        "ceph_pool_wr_bytes",
        "ceph_pool_stored",
        "ceph_pool_stored_raw",
        "ceph_pool_bytes_used",
        "ceph_pool_max_avail",
        "ceph_pool_percent_used",
        "ceph_pool_objects",
        *(f"ceph_pg_{state}" for state in PG_STATES),
    }
)
CEPH_PREFIXES = ("ceph_rbd_",)


# instance disk totals without stacked/partition double-counting (D40)
DISK_TOTALS = {
    "disk_read": "mcui_instance_disk_read_bytes_total",
    "disk_written": "mcui_instance_disk_written_bytes_total",
}


def _keep_instance_sample(name: str, labels: Mapping[str, str]) -> bool:
    if name.startswith("lxd_filesystem_"):
        mount = labels.get("mountpoint", "")
        if labels.get("fstype") in SKIP_FSTYPES:
            return False
        if mount.startswith(KEEP_BLOCK_UNDER) and labels.get("device", "").startswith("/dev/"):
            return True
        return not mount.startswith(SKIP_MOUNT_PREFIXES)
    if name.startswith("lxd_network_"):
        return not labels.get("device", "").startswith(SKIP_NIC_PREFIXES)
    if name.startswith("lxd_disk_"):
        return stored_disk(labels.get("device", ""))
    return True


def _labels(member: str, labels: Mapping[str, str]) -> dict[str, str]:
    out = {k: v for k, v in labels.items() if k not in DROP_LABELS}
    out["member"] = member
    return out


def lxd_samples(member: str, exp: Exposition) -> list[Sample]:
    out: list[Sample] = []
    for s in exp.samples:
        if s.name in INSTANCE:
            if "name" in s.labels and _keep_instance_sample(s.name, s.labels):
                out.append(Sample(s.name, _labels(member, s.labels), s.value, INSTANCE[s.name]))
        elif s.name in DAEMON and "name" not in s.labels:
            out.append(Sample(s.name, _labels(member, s.labels), s.value, DAEMON[s.name]))
    for (project, name), t in instance_totals(exp).items():
        base = {"member": member, "project": project, "name": name, "type": t.type}
        if "cpu_busy" in t.values:
            out.append(
                Sample(
                    "mcui_instance_cpu_busy_seconds_total", base, t.values["cpu_busy"], "counter"
                )
            )
        cpus = effective_cpus(t)
        if cpus:
            out.append(Sample("mcui_instance_cpus", base, cpus))
        used = memory_used(t)
        if used is not None:
            out.append(Sample("mcui_instance_memory_used_bytes", base, used))
        for key, metric in DISK_TOTALS.items():
            if key in t.values:
                out.append(Sample(metric, base, t.values[key], "counter"))
    return out


def ceph_samples(exp: Exposition) -> list[Sample]:
    out = []
    for s in exp.samples:
        if s.name in CEPH or s.name.startswith(CEPH_PREFIXES):
            kind: Kind = "counter" if exp.types.get(s.name) == "counter" else "gauge"
            out.append(Sample(s.name, dict(s.labels), s.value, kind))
    return out


_SYSINFO: dict[str, str] = {
    "total_ram": "mcui_member_memory_total_bytes",
    "free_ram": "mcui_member_memory_free_bytes",
    "buffered_ram": "mcui_member_memory_buffered_bytes",
    "shared_ram": "mcui_member_memory_shared_bytes",
    "total_swap": "mcui_member_swap_total_bytes",
    "free_swap": "mcui_member_swap_free_bytes",
    "processes": "mcui_member_processes",
    "uptime": "mcui_member_uptime_seconds",
    "logical_cpus": "mcui_member_cpus",
}


def _num(v: Any) -> float | None:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None


def member_state_samples(member: str, state: Mapping[str, Any]) -> list[Sample]:
    """Host load/memory and per-member storage pool usage (MO-4, MO-6)."""
    out: list[Sample] = []
    m = {"member": member}
    si = state.get("sysinfo") or {}
    for key, name in _SYSINFO.items():
        v = _num(si.get(key))
        if v is not None:
            out.append(Sample(name, m, v))
    for period, v in zip((1, 5, 15), si.get("load_averages") or [], strict=False):
        if (f := _num(v)) is not None:
            out.append(Sample(f"mcui_member_load{period}", m, f))
    for pool, d in (state.get("storage_pools") or {}).items():
        labels = {"member": member, "pool": pool}
        for part in ("space", "inodes"):
            usage = d.get(part) or {}
            total = _num(usage.get("total"))
            if not total:
                continue
            out.append(Sample(f"mcui_pool_{part}_total", labels, total))
            used = _num(usage.get("used"))
            if used is not None:
                out.append(Sample(f"mcui_pool_{part}_used", labels, used))
    return out


_NET_COUNTERS = {
    "bytes_received": "mcui_host_network_receive_bytes_total",
    "bytes_sent": "mcui_host_network_transmit_bytes_total",
    "packets_received": "mcui_host_network_receive_packets_total",
    "packets_sent": "mcui_host_network_transmit_packets_total",
    "errors_received": "mcui_host_network_receive_errs_total",
    "errors_sent": "mcui_host_network_transmit_errs_total",
    "packets_dropped_inbound": "mcui_host_network_receive_drop_total",
    "packets_dropped_outbound": "mcui_host_network_transmit_drop_total",
}
HOST_NETWORK_TYPES = frozenset({"physical", "bond", "vlan", "bridge"})


def network_state_samples(member: str, network: str, state: Mapping[str, Any]) -> list[Sample]:
    """Host interface counters (MO-8). OVN networks report zeros, so they're skipped."""
    labels = {"member": member, "network": network}
    counters = state.get("counters") or {}
    out = []
    for key, name in _NET_COUNTERS.items():
        v = _num(counters.get(key))
        if v is not None:
            out.append(Sample(name, labels, v, "counter"))
    return out


def host_networks(networks: Iterable[Mapping[str, Any]]) -> list[str]:
    return sorted(
        n["name"]
        for n in networks
        if n.get("type") in HOST_NETWORK_TYPES and not str(n["name"]).startswith(SKIP_NIC_PREFIXES)
    )


NODE_GAUGES = frozenset(
    {
        "node_memory_MemTotal_bytes",
        "node_memory_MemAvailable_bytes",
        "node_memory_MemFree_bytes",
        "node_memory_Cached_bytes",
        "node_memory_Buffers_bytes",
        "node_memory_SwapTotal_bytes",
        "node_memory_SwapFree_bytes",
        "node_zfs_arc_size",
        "node_timex_offset_seconds",
        "node_timex_sync_status",
    }
)
NODE_COUNTERS = frozenset(
    {
        "node_pressure_cpu_waiting_seconds_total",
        "node_pressure_memory_waiting_seconds_total",
        "node_pressure_memory_stalled_seconds_total",
        "node_pressure_io_waiting_seconds_total",
        "node_pressure_io_stalled_seconds_total",
        "node_vmstat_oom_kill",
        "node_vmstat_pswpin",
        "node_vmstat_pswpout",
    }
)
NODE_DISK = frozenset(
    {
        "node_disk_read_bytes_total",
        "node_disk_written_bytes_total",
        "node_disk_reads_completed_total",
        "node_disk_writes_completed_total",
        "node_disk_io_time_seconds_total",
    }
)
NODE_FS = frozenset({"node_filesystem_size_bytes", "node_filesystem_avail_bytes"})
NODE_SKIP_MOUNT_PREFIXES = (*SKIP_MOUNT_PREFIXES, "/var/snap", "/var/lib/snapd")


def node_samples(member: str, exp: Exposition) -> list[Sample]:
    """Host series from node_exporter (MO-12, D58): CPU seconds summed over CPUs per
    mode, memory, real filesystems, whole host disks, pressure and swap activity."""
    m = {"member": member}
    out: list[Sample] = []
    cpu: dict[str, float] = {}
    for s in exp.samples:
        if s.name == "node_cpu_seconds_total":
            mode = s.labels.get("mode", "")
            cpu[mode] = cpu.get(mode, 0.0) + s.value
        elif s.name in NODE_GAUGES:
            out.append(Sample(s.name, m, s.value))
        elif s.name in NODE_COUNTERS:
            out.append(Sample(s.name, m, s.value, "counter"))
        elif s.name in NODE_DISK:
            if host_disk(dev := s.labels.get("device", "")):
                out.append(Sample(s.name, {**m, "device": dev}, s.value, "counter"))
        elif s.name in NODE_FS:
            mount, fstype = s.labels.get("mountpoint", ""), s.labels.get("fstype", "")
            if fstype not in SKIP_FSTYPES and not mount.startswith(NODE_SKIP_MOUNT_PREFIXES):
                out.append(Sample(s.name, {**m, "mountpoint": mount, "fstype": fstype}, s.value))
    out += [
        Sample("node_cpu_seconds_total", {**m, "mode": mode}, v, "counter")
        for mode, v in cpu.items()
    ]
    return out
