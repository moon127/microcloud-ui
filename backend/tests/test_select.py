import json
from collections import Counter
from pathlib import Path

from mcui.metrics.prom import parse
from mcui.metrics.select import (
    ceph_samples,
    host_networks,
    lxd_samples,
    member_state_samples,
    network_state_samples,
)
from tests.conftest import fixture_text

RECORDED = Path(__file__).parent / "fixtures" / "recorded"


def names(samples):
    return Counter(s.name for s in samples)


def test_lxd_samples_from_fixture():
    out = lxd_samples("m1", parse(fixture_text("metrics_m1_t0.txt")))
    by = {(s.name, s.labels.get("name"), s.labels.get("device")): s for s in out}
    c1 = by[("mcui_instance_cpu_busy_seconds_total", "c1", None)]
    assert c1.value == 30 and c1.kind == "counter"
    assert c1.labels == {"member": "m1", "project": "default", "name": "c1", "type": "container"}
    assert by[("mcui_instance_cpus", "v1", None)].value == 2  # VM vCPUs counted
    assert by[("mcui_instance_memory_used_bytes", "c1", None)].value == 5e8
    assert ("lxd_network_receive_bytes_total", "c1", "lo") not in by
    assert ("lxd_network_receive_bytes_total", "c1", "eth0") in by
    assert by[("lxd_disk_read_bytes_total", "c1", "sda")].kind == "counter"
    assert by[("lxd_uptime_seconds", None, None)].labels == {"member": "m1"}
    ongoing = [s for s in out if s.name == "lxd_api_requests_ongoing"]
    assert {s.labels["entity_type"] for s in ongoing} == {"instance", "server"}
    assert "lxd_cpu_seconds_total" not in names(out) and "lxd_go_goroutines" in names(out)


def test_lxd_samples_from_real_cluster():
    total_in = total_out = 0
    for path in sorted(RECORDED.glob("metrics_*.txt")):
        exp = parse(path.read_text())
        out = lxd_samples("m", exp)
        total_in += len(exp.samples)
        total_out += len(out)
        for s in out:
            assert "state" not in s.labels
            if s.name.startswith("lxd_filesystem_"):
                assert s.labels["fstype"] != "tmpfs"
                assert not s.labels["mountpoint"].startswith(("/run", "/var/lib/containers"))
            if s.name.startswith("lxd_network_"):
                assert not s.labels["device"].startswith(("lo", "veth"))
    assert 0 < total_out < total_in * 0.6  # D34: well under the full exposition


def test_filesystem_filter():
    fs = (
        'lxd_filesystem_size_bytes{name="a",project="p",device="x",fstype="%s",mountpoint="%s"} 1\n'
    )
    exp = parse(
        fs % ("tmpfs", "/x")
        + fs % ("ext4", "/run/y")
        + fs % ("ext4", "/data")
        + 'lxd_disk_read_bytes_total{name="a",project="p",device="loop0"} 1\n'
        + 'lxd_disk_read_bytes_total{name="a",project="p",device="sda1"} 1\n'
        + 'lxd_disk_read_bytes_total{name="a",project="p",device="nvme0n1p2"} 1\n'
        + 'lxd_disk_read_bytes_total{name="a",project="p",device="sda"} 1\n'
        + 'lxd_disk_read_bytes_total{name="a",project="p",device="rbd0"} 1\n'
        + 'lxd_disk_read_bytes_total{name="a",project="p",device="dm-1"} 4\n'
    )
    kept = {
        (s.name, s.labels.get("mountpoint"))
        for s in lxd_samples("m", exp)
        if s.name.startswith("lxd_")
    }
    assert kept == {("lxd_filesystem_size_bytes", "/data"), ("lxd_disk_read_bytes_total", None)}
    devices = {s.labels["device"] for s in lxd_samples("m", exp) if s.name.startswith("lxd_disk")}
    assert devices == {"sda", "rbd0", "dm-1"}  # partitions and loop devices skipped
    total = [s for s in lxd_samples("m", exp) if s.name == "mcui_instance_disk_read_bytes_total"]
    assert [(t.value, t.kind) for t in total] == [(2.0, "counter")]  # sda + rbd0; dm left out


def test_ceph_samples_from_real_cluster():
    exp = parse((RECORDED / "ceph_mgr_active_perf_rbd_on.txt").read_text())
    out = ceph_samples(exp)
    n = names(out)
    assert n["ceph_rbd_read_ops"] == 34
    assert n["ceph_osd_apply_latency_ms"] == 6 and n["ceph_pool_metadata"] == 4
    assert not any(k.startswith(("ceph_bluestore", "ceph_bluefs", "ceph_paxos")) for k in n)
    kinds = {s.name: s.kind for s in out}
    assert kinds["ceph_osd_op_r"] == "counter" and kinds["ceph_health_status"] == "gauge"
    assert len(out) < len(exp.samples) / 4


def test_member_state_samples_from_real_cluster():
    state = json.loads(
        (RECORDED / "1.0_cluster_members_gw-ips-microcloud-1_state.json").read_text()
    )
    out = {
        (s.name, s.labels.get("pool")): s.value
        for s in member_state_samples("m1", state["metadata"])
    }
    assert out[("mcui_member_cpus", None)] == 8
    assert ("mcui_member_load1", None) in out and ("mcui_member_load15", None) in out
    assert ("mcui_pool_space_used", "remote") in out
    assert ("mcui_pool_space_total", "remote-fs") in out
    assert ("mcui_pool_space_used", "remote-fs") not in out  # CephFS reports no "used"
    assert ("mcui_pool_inodes_total", "remote") not in out  # total 0: skipped


def test_member_state_tolerates_missing_and_odd_values():
    out = member_state_samples("m", {"sysinfo": {"total_ram": True, "load_averages": [1, "x"]}})
    assert [(s.name, s.value) for s in out] == [("mcui_member_load1", 1.0)]
    assert member_state_samples("m", {}) == []


def test_network_state_and_host_networks():
    state = {"counters": {"bytes_received": 10, "bytes_sent": 20, "packets_received": 1}}
    out = network_state_samples("m1", "bond-core", state)
    assert {s.name for s in out} == {
        "mcui_host_network_receive_bytes_total",
        "mcui_host_network_transmit_bytes_total",
        "mcui_host_network_receive_packets_total",
    }
    assert all(
        s.kind == "counter" and s.labels == {"member": "m1", "network": "bond-core"} for s in out
    )
    assert network_state_samples("m", "x", {}) == []
    nets = json.loads((RECORDED / "1.0_networks_recursion_1.json").read_text())["metadata"]
    hosts = host_networks(nets)
    assert "bond-core" in hosts and "UPLINK" in hosts and "eno1" in hosts
    assert not {"default", "lo", "br-int", "ovs-system"} & set(hosts)
