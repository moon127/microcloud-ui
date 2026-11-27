"""Alerts (Phase 8a, D91): the store, the engine, every rule, the service and the API.
Shapes as LXD 6.10, the Ceph mgr and node_exporter served them on the test cloud."""

import asyncio
import ssl
from pathlib import Path
from types import SimpleNamespace

import pytest
import respx

from mcui.alerts.engine import AlertEngine
from mcui.alerts.rules import (
    MARGIN,
    RULES,
    Context,
    NoData,
    ceph_health,
    ceph_raw,
    cert_days,
    cert_expiry,
    collection,
    evaluate,
    host_clock,
    host_fs,
    host_memory,
    instance_oom,
    instance_rootfs,
    local_pool,
    lxd_security,
    member_status,
    network_addresses,
    ovn_uplink,
    project_limits,
)
from mcui.alerts.store import Alert, AlertStore
from mcui.config import DEFAULT_THRESHOLDS, Settings
from mcui.main import create_app
from mcui.metrics.collector import TaskStatus
from mcui.metrics.prom import parse
from mcui.metrics.store import QueryResult, SeriesData
from mcui.services import build_services
from tests.conftest import EP1, fast_store, lxd_err, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair
from tests.test_api_instances import admin, app, services

__all__ = ["app", "services"]  # fixtures

NOW = 1_800_000_000.0


class Clock:
    def __init__(self, t=NOW):
        self.t = t

    def __call__(self):
        return self.t


def alert(rule="r", subject="s", severity="warning", project=None, value=1):
    return Alert(rule, subject, severity, f"{rule} {subject}", "d", "/x", project, {"v": value})


# --- store and engine ---------------------------------------------------------------------


def test_store_and_engine():
    clock = Clock()
    store = AlertStore(fast_store().db, clock)
    eng = AlertEngine(store, clock)
    # needs 60 s before it fires
    assert eng.apply({"r": [alert()]}, {"r": 60}) == []
    clock.t += 30
    assert eng.apply({"r": [alert()]}, {"r": 60}) == []
    clock.t += 30
    fired = eng.apply({"r": [alert()]}, {"r": 60})
    assert [f["key"] for f in fired] == ["r:s"] and fired[0]["data"] == {"v": 1}
    # refreshed while true
    clock.t += 10
    eng.apply({"r": [alert(severity="critical", value=2)]}, {"r": 60})
    row = store.list(True)[0]
    assert row["severity"] == "critical" and row["data"] == {"v": 2} and row["last_seen"] == clock.t
    # a rule that couldn't look leaves it alone
    eng.apply({"r": None}, {"r": 60})
    assert store.list(True)
    # acknowledged, then cleared
    assert store.acknowledge(row["id"], "alice")
    clock.t += 10
    eng.apply({"r": []}, {"r": 60})
    assert store.list(True) == [] and store.list(False)[0]["ack_by"] == "alice"
    assert not store.acknowledge(row["id"], "bob")  # cleared: nothing to acknowledge
    # pending ones that go away are forgotten, others' untouched
    eng.apply({"r": [alert(subject="t")], "q": [alert("q")]}, {"r": 60})
    assert "r:t" in eng.pending and store.list(True)[0]["key"] == "q:s"
    eng.apply({"r": []}, {"r": 60})
    assert "r:t" not in eng.pending
    # paging and pruning
    assert store.list(False, before=row["id"]) == []
    clock.t += 91 * 86400
    eng.apply({"q": []}, {})
    assert store.prune(90) == 1 and store.list(False)[0]["key"] == "q:s"
    assert store.get(9999) is None


def test_engine_review_fixes():
    """D91 review: escalation undoes an acknowledgement; unknown subjects stay open; a
    round that couldn't look restarts pending timers; project filter before the limit."""
    clock = Clock()
    store = AlertStore(fast_store().db, clock)
    eng = AlertEngine(store, clock)
    eng.apply({"r": [alert(subject="m1"), alert(subject="m10")]}, {})
    ids = {r["subject"]: r["id"] for r in store.list(True)}
    store.acknowledge(ids["m1"], "alice")
    eng.apply({"r": [alert(subject="m1"), alert(subject="m10")]}, {})  # still a warning
    assert store.get(ids["m1"])["ack_by"] == "alice"
    eng.apply({"r": [alert(subject="m1", severity="critical"), alert(subject="m10")]}, {})
    assert store.get(ids["m1"])["ack_by"] is None  # worse: news again
    store.acknowledge(ids["m1"], "alice")
    eng.apply({"r": [alert(subject="m1", severity="critical"), alert(subject="m10")]}, {})
    assert store.get(ids["m1"])["ack_by"] == "alice"  # still critical: stays acknowledged
    # m1 unknown this round: kept; m10 (not m1's prefix) cleared
    eng.apply({"r": []}, {}, {"r:m1"})
    assert [r["subject"] for r in store.list(True)] == ["m1"]
    eng.apply({"r": []}, {}, {"r:"})  # a prefix ending in ":" covers the rule
    assert store.list(True)
    eng.apply({"r": []}, {})
    assert store.list(True) == []
    # pending, then a blind round: the timer starts again
    eng.apply({"p": [alert("p")]}, {"p": 60})
    clock.t += 120
    eng.apply({"p": None}, {"p": 60})
    assert eng.apply({"p": [alert("p")]}, {"p": 60}) == []
    clock.t += 60
    assert eng.apply({"p": [alert("p")]}, {"p": 60})
    # a project user's list is filtered before the limit
    for i in range(5):
        store.fire(alert("x", f"s{i}"))
    web = store.fire(alert("instance_oom", "web/c1", project="web"))
    assert [r["id"] for r in store.list(True, limit=2, projects=frozenset({"web"}))] == [web]
    assert store.list(True, projects=frozenset()) == []
    assert store.resolve_all() == 7 and store.list(True) == []


# --- rules ----------------------------------------------------------------------------------

CEPH = parse(
    """ceph_health_detail{name="MON_DISK_LOW",severity="HEALTH_WARN"} 1.0
ceph_health_detail{name="OSD_NEARFULL",severity="HEALTH_WARN"} 0.0
ceph_health_detail{name="OSD_FULL",severity="HEALTH_ERR"} 1.0
ceph_health_detail{name="NEW_CHECK",severity="HEALTH_WARN"} 1.0
ceph_cluster_total_bytes 1000.0
ceph_cluster_total_used_raw_bytes 800.0
"""
)
NODE = parse(
    """node_filesystem_size_bytes{device="/dev/sda2",fstype="ext4",mountpoint="/"} 100
node_filesystem_avail_bytes{device="/dev/sda2",fstype="ext4",mountpoint="/"} 4
node_filesystem_free_bytes{device="/dev/sda2",fstype="ext4",mountpoint="/"} 9
node_filesystem_size_bytes{device="tmpfs",fstype="tmpfs",mountpoint="/run"} 100
node_filesystem_avail_bytes{device="tmpfs",fstype="tmpfs",mountpoint="/run"} 1
node_filesystem_size_bytes{device="/dev/sdb",fstype="ext4",mountpoint="/srv"} 100
node_memory_MemTotal_bytes 100
node_memory_MemAvailable_bytes 8
node_timex_sync_status 0
"""
)


class FakeMetrics:
    def __init__(self, series):
        self.series = series

    async def query(self, name, match, start, end, **kw):
        return QueryResult(name, "gauge", 0, kw.get("fn", "value"), self.series.get(name, []))


def svc(**over):
    settings = over.pop("settings", Settings())
    col = SimpleNamespace(
        ceph_last=SimpleNamespace(ok=True, exposition=CEPH),
        ceph_at=NOW - 10,
        state_last={},
        status={},
    )
    base = dict(settings=settings, collector=col, ceph=object(), node=None, lxd=None, metrics=None)
    base.update(over)
    return SimpleNamespace(**base)


def ctx(services, open_=(), thresholds=None):
    sev = open_ if isinstance(open_, dict) else dict.fromkeys(open_, "warning")
    return Context(services, sev, thresholds or dict(DEFAULT_THRESHOLDS), NOW)


async def test_ceph_rules():
    out = await ceph_health(ctx(svc()))
    assert [(a.subject, a.severity) for a in out] == [
        ("MON_DISK_LOW", "warning"),
        ("OSD_FULL", "critical"),
        ("NEW_CHECK", "warning"),
    ]
    assert out[0].title == "A Ceph monitor's disk is low on space"
    assert out[2].title == "Ceph reports NEW_CHECK"
    [raw] = await ceph_raw(ctx(svc()))
    assert raw.severity == "warning" and raw.data["value"] == 80.0  # 75 <= 80 < 85
    # no Ceph monitored: nothing; stale or failed: couldn't look
    assert await ceph_health(ctx(svc(ceph=None))) == []
    s = svc()
    s.collector.ceph_at = NOW - 3600
    with pytest.raises(NoData):
        await ceph_raw(ctx(s))
    s = svc()
    s.collector.ceph_last = SimpleNamespace(ok=False, exposition=None)
    with pytest.raises(NoData):
        await ceph_health(ctx(s))
    s = svc()
    s.collector.ceph_last = SimpleNamespace(ok=True, exposition=parse("ceph_x 1\n"))
    assert await ceph_raw(ctx(s)) == []


def test_hysteresis():
    c = ctx(svc())
    assert c.level("local_pool", "k", 79) is None
    assert c.level("local_pool", "k", 80) == "warning"
    c.open = {"k": "warning"}
    assert c.level("local_pool", "k", 80 - MARGIN) == "warning"  # stays until 2 below
    assert c.level("local_pool", "k", 80 - MARGIN - 0.1) is None
    # a warning turns critical at the threshold itself, and steps down 2 below it
    assert c.level("local_pool", "k", 90 - MARGIN) == "warning"
    assert c.level("local_pool", "k", 90) == "critical"
    c.open = {"k": "critical"}
    assert c.level("local_pool", "k", 90 - MARGIN) == "critical"
    assert c.level("local_pool", "k", 90 - MARGIN - 0.1) == "warning"
    assert c.level("cert_days", "c", 40, lower_is_worse=True) is None
    assert c.level("cert_days", "c", 20, lower_is_worse=True) == "warning"
    assert c.level("cert_days", "c", 5, lower_is_worse=True) == "critical"


@respx.mock
async def test_local_pools():
    from mcui.lxd.client import LXDClient

    lxd = LXDClient([EP1], verify=ssl.create_default_context())
    respx.get(f"{EP1}/1.0/storage-pools").mock(
        return_value=lxd_ok(
            [{"name": "local", "driver": "zfs"}, {"name": "remote", "driver": "ceph"}]
        )
    )
    s = svc(lxd=lxd)
    s.collector.state_last = {
        "m1": (
            NOW - 30,
            {
                "storage_pools": {
                    "local": {"space": {"used": 91, "total": 100}},
                    "remote": {"space": {"used": 99, "total": 100}},  # Ceph: its own rules
                    "empty": {"space": {}},
                }
            },
        ),
        "m2": (NOW, {"storage_pools": {"local": {"space": {"used": 10, "total": 100}}}}),
        "m3": (NOW - 3600, {"storage_pools": {"local": {"space": {"used": 99, "total": 100}}}}),
    }
    c = ctx(s)
    [a] = await local_pool(c)
    assert (a.subject, a.severity, a.link) == ("m1/local", "critical", "/storage/pools/local")
    assert c.unknown == {"local_pool:m3/"}  # an hour old: unknown, not cleared
    s.collector.state_last = {}
    with pytest.raises(NoData):
        await local_pool(ctx(s))
    # collected fine, but no member state: a standalone server, nothing to report
    s.collector.status = {"state": TaskStatus(last_ok=NOW - 5)}
    assert await local_pool(ctx(s)) == []
    s.collector.status = {"state": TaskStatus(last_ok=NOW - 500, last_error="boom")}
    with pytest.raises(NoData):  # worked once, failing now: can't look
        await local_pool(ctx(s))
    assert await local_pool(ctx(svc())) == []  # no LXD
    await lxd.aclose()


async def test_host_rules():
    node = SimpleNamespace(
        last={
            "m1": SimpleNamespace(ok=True, absent=False, exposition=NODE),
            "m2": SimpleNamespace(ok=False, absent=True, exposition=None, error="x"),
        }
    )
    s = svc(node=node)
    [fs] = await host_fs(ctx(s))
    assert (fs.subject, fs.severity) == ("m1:/", "critical")  # tmpfs and no-avail skipped
    assert fs.data["value"] == pytest.approx(91 / 95 * 100, abs=0.1)  # as df: reserved not counted
    [mem] = await host_memory(ctx(s))
    assert (mem.subject, mem.severity) == ("m1", "warning")  # 92%
    [clk] = await host_clock(ctx(s))
    assert clk.subject == "m1"
    # one member's exporter failing: only its alerts are unknown, for every host rule
    node.last["m2"] = SimpleNamespace(ok=False, absent=False, error="HTTP 401", exposition=None)
    for rule, name in (
        (host_fs, "host_fs"),
        (host_memory, "host_memory"),
        (host_clock, "host_clock"),
    ):
        c = ctx(s)
        await rule(c)
        assert c.unknown == {f"{name}:m2", f"{name}:m2:"}


@respx.mock
async def test_host_rules_absent_exporter():
    """Nothing listening: unknown while LXD says the member is down (it can't be read),
    cleared while it's online (node_exporter went away; found in the VM test)."""
    from mcui.lxd.client import LXDClient

    lxd = LXDClient([EP1], verify=ssl.create_default_context())
    respx.get(f"{EP1}/1.0").mock(
        return_value=lxd_ok({"environment": {"server_clustered": True}, "api_extensions": []})
    )
    status = {"m2": "Online"}
    respx.get(f"{EP1}/1.0/cluster/members").mock(
        side_effect=lambda req: lxd_ok(
            [
                {"server_name": "m1", "url": "https://192.0.2.1:8443", "status": "Online"},
                {"server_name": "m2", "url": "https://192.0.2.2:8443", "status": status["m2"]},
            ]
        )
    )
    node = SimpleNamespace(
        last={
            "m1": SimpleNamespace(ok=True, absent=False, exposition=NODE),
            "m2": SimpleNamespace(ok=False, absent=True, exposition=None, error="refused"),
        }
    )
    c = ctx(svc(node=node, lxd=lxd))
    await host_fs(c)
    assert c.unknown == set()  # online, nothing listening: its alerts clear
    status["m2"] = "Offline"
    c = ctx(svc(node=node, lxd=lxd))
    await host_fs(c)
    assert c.unknown == {"host_fs:m2", "host_fs:m2:"}
    respx.get(f"{EP1}/1.0/cluster/members").mock(return_value=lxd_err("down", 503))
    c = ctx(svc(node=node, lxd=lxd))
    await host_memory(c)
    assert c.unknown == {"host_memory:m2", "host_memory:m2:"}  # can't tell: kept
    await lxd.aclose()
    assert await host_memory(ctx(svc())) == []
    quiet = SimpleNamespace(last={"m1": SimpleNamespace(ok=True, exposition=parse("node_x 1\n"))})
    assert await host_memory(ctx(svc(node=quiet))) == []
    assert await host_clock(ctx(svc(node=quiet))) == []


@respx.mock
async def test_members_and_security():
    from mcui.lxd.client import LXDClient

    lxd = LXDClient([EP1], verify=ssl.create_default_context())
    respx.get(f"{EP1}/1.0").mock(
        side_effect=lambda req: lxd_ok(
            {
                "environment": {
                    "server_clustered": True,
                    "server_version": "5.21.2" if "m2" in str(req.url) else "6.10.1",
                },
                "api_extensions": [],
            }
        )
    )
    respx.get(f"{EP1}/1.0/cluster/members").mock(
        return_value=lxd_ok(
            [
                {"server_name": "m1", "url": "https://192.0.2.1:8443", "status": "Online"},
                {"server_name": "m2", "url": "https://192.0.2.2:8443", "status": "Evacuated"},
                {
                    "server_name": "m3",
                    "url": "https://192.0.2.3:8443",
                    "status": "Offline",
                    "message": "No heartbeat",
                },
            ]
        )
    )
    out = await member_status(ctx(svc(lxd=lxd)))
    assert [(a.subject, a.severity) for a in out] == [("m2", "warning"), ("m3", "critical")]
    assert "No heartbeat" in out[1].detail
    import mcui.alerts.rules as r

    r._versions.clear()
    sec = await lxd_security(ctx(svc(lxd=lxd)))
    assert [a.subject for a in sec] == ["m2"] and "5.21.2" in sec[0].detail
    calls = respx.calls.call_count
    assert [a.subject for a in await lxd_security(ctx(svc(lxd=lxd)))] == ["m2"]
    # cached: no member asked again
    assert not [c for c in respx.calls[calls:] if "target" in str(c.request.url)]
    assert await member_status(ctx(svc())) == [] and await lxd_security(ctx(svc())) == []
    await lxd.aclose()


@respx.mock
async def test_lxd_security_standalone_and_unreachable():
    import mcui.alerts.rules as r
    from mcui.lxd.client import LXDClient

    r._versions.clear()
    lxd = LXDClient([EP1], verify=ssl.create_default_context())
    top = respx.get(f"{EP1}/1.0").mock(
        return_value=lxd_ok(
            {"environment": {"server_clustered": False, "server_version": "5.21.1"}}
        )
    )
    [a] = await lxd_security(ctx(svc(lxd=lxd)))
    assert a.subject == "server" and top.call_count == 1  # no target on a standalone server
    r._versions.clear()

    def by_target(req):
        if "m2" in str(req.url):
            return lxd_err("down", 503)
        env = {"server_clustered": True, "server_version": "6.10.1"}
        return lxd_ok({"environment": env})

    top.mock(side_effect=by_target)
    respx.get(f"{EP1}/1.0/cluster/members").mock(
        return_value=lxd_ok(
            [
                {"server_name": "m1", "url": "https://192.0.2.1:8443", "status": "Online"},
                {"server_name": "m2", "url": "https://192.0.2.2:8443", "status": "Offline"},
            ]
        )
    )
    c = ctx(svc(lxd=lxd))
    assert await lxd_security(c) == [] and c.unknown == {"lxd_security:m2"}
    r._versions.clear()
    await lxd.aclose()


async def test_instance_rules():
    metrics = FakeMetrics(
        {
            "lxd_memory_OOM_kills_total": [
                SeriesData({"project": "web", "name": "c1"}, [(1, 0.0), (2, 0.1)]),
                SeriesData({"project": "web", "name": "c2"}, [(1, 0.0), (2, None)]),
            ],
            "lxd_filesystem_size_bytes": [
                SeriesData({"project": "web", "name": "c1"}, [(1, 100.0)]),
                SeriesData({"project": "web", "name": "c2"}, [(1, 100.0)]),
                SeriesData({"project": "web", "name": "c3"}, []),
            ],
            "lxd_filesystem_avail_bytes": [
                SeriesData({"project": "web", "name": "c1"}, [(1, 7.0)]),
                SeriesData({"project": "web", "name": "c2"}, [(1, 50.0)]),
            ],
        }
    )
    [oom] = await instance_oom(ctx(svc(metrics=metrics)))
    assert (oom.subject, oom.project, oom.link) == ("web/c1", "web", "/instances/web/c1")
    [fs] = await instance_rootfs(ctx(svc(metrics=metrics)))
    assert (fs.subject, fs.severity, fs.project) == ("web/c1", "warning", "web")
    assert await instance_oom(ctx(svc())) == [] and await instance_rootfs(ctx(svc())) == []


@respx.mock
async def test_project_and_network_rules(monkeypatch):
    from mcui.lxd.client import LXDClient

    lxd = LXDClient([EP1], verify=ssl.create_default_context())
    respx.get(f"{EP1}/1.0/projects").mock(
        return_value=lxd_ok(
            [
                {"name": "default", "config": {}},
                {"name": "web", "config": {"limits.disk": "10GiB", "features.networks": "true"}},
            ]
        )
    )
    respx.get(f"{EP1}/1.0/projects/web/state").mock(
        return_value=lxd_ok(
            {
                "resources": {
                    # as LXD 6.10 sends them (capitalised, seen live)
                    "disk": {"Limit": 100, "Usage": 99},
                    "memory": {"Limit": -1, "Usage": 5},
                    "cpu": {"Limit": 10, "Usage": 1},
                    "odd": {"Limit": 1, "Usage": 1},
                    "virtual-machines": {"Limit": 0, "Usage": 0},
                }
            }
        )
    )
    [lim] = await project_limits(ctx(svc(lxd=lxd)))
    assert (lim.subject, lim.severity, lim.project) == ("web/disk", "critical", "web")
    # as LXD 6.10 sends them: "addresses", no project, the project in used_by
    respx.get(f"{EP1}/1.0/network-allocations").mock(
        return_value=lxd_ok(
            [{"addresses": "10.0.0.1/26", "network": "br0", "type": "network"}]
            + [
                {
                    "addresses": f"10.0.0.{i}/32",
                    "network": "br0",
                    "type": "instance",
                    "used_by": f"/1.0/instances/c{i}",
                }
                for i in range(2, 60)
            ]
            + [
                {"addresses": "2001:db8::1/128", "network": "br0", "type": "instance"},
                {"addresses": "", "network": "x", "type": "instance"},
                # an LB's listen address comes from the uplink: not br0's
                {
                    "addresses": "192.0.2.50/32",
                    "network": "br0",
                    "type": "network-load-balancer",
                    "used_by": "/1.0/networks/br0/load-balancers/192.0.2.50",
                },
                # web's own br0: a different network of the same name
                {
                    "addresses": "10.1.0.2/32",
                    "network": "br0",
                    "type": "instance",
                    "used_by": "/1.0/instances/w1?project=web",
                },
            ]
        )
    )
    respx.get(f"{EP1}/1.0/networks", params={"project": "default"}).mock(
        return_value=lxd_ok(
            [
                {"name": "br0", "type": "bridge", "config": {"ipv4.address": "10.0.0.1/26"}},
                {"name": "lo", "type": "loopback", "config": {}},
                {"name": "p2p", "type": "bridge", "config": {"ipv4.address": "10.9.0.1/31"}},
                {"name": "none", "type": "ovn", "config": {"ipv4.address": "none"}},
                {"name": "bad", "type": "bridge", "config": {"ipv4.address": "10.0.0.x/24"}},
                {
                    "name": "UPLINK",
                    "type": "physical",
                    "config": {"ipv4.ovn.ranges": "192.0.2.10-192.0.2.19"},
                },
                {"name": "v6only", "type": "physical", "config": {}},
                {
                    "name": "o1",
                    "type": "ovn",
                    "config": {
                        "network": "UPLINK",
                        "volatile.network.ipv4.address": "192.0.2.10",
                        "ipv4.address": "10.5.0.1/24",
                    },
                },
            ]
        )
    )
    respx.get(f"{EP1}/1.0/networks", params={"project": "web"}).mock(
        return_value=lxd_ok(
            [{"name": "br0", "type": "bridge", "config": {"ipv4.address": "10.1.0.1/24"}}]
            + [
                {
                    "name": f"w{i}",
                    "type": "ovn",
                    "config": {
                        "network": "UPLINK",
                        "volatile.network.ipv4.address": f"192.0.2.{11 + i}",
                    },
                }
                for i in range(7)
            ]
        )
    )
    c = ctx(svc(lxd=lxd))
    [net] = await network_addresses(c)
    # 58 instances of 61 usable; the gateway, the LB and web's address not counted
    assert (net.subject, net.severity, net.project) == ("default/br0", "critical", None)
    assert net.detail.startswith("58 of 61")
    [up] = await ovn_uplink(c)  # 8 routers of 10 addresses
    assert (up.subject, up.severity) == ("UPLINK", "warning")
    assert up.detail.startswith("8 of 10 addresses in ipv4.ovn.ranges used")
    # one listing of projects and networks for all three rules in a round
    assert respx.calls.call_count == 6  # projects, state, 2 network lists, allocations...
    for rule in (project_limits, network_addresses, ovn_uplink):
        assert await rule(ctx(svc())) == []
    await lxd.aclose()


async def test_cert_expiry(certs, monkeypatch, tmp_path):
    days = {"a": 400.0, "b": 20.0, "c": -2.0}
    monkeypatch.setattr("mcui.alerts.rules.cert_days", lambda p: days[Path(p).name[0]])
    a, b, c = (tmp_path / "a.crt", tmp_path / "b.crt", tmp_path / "c.crt")
    for f in (a, b, c):
        f.write_text("x")
    s = svc(
        settings=SimpleNamespace(
            lxd=SimpleNamespace(server_cert=a, client_cert=b),
            metrics=SimpleNamespace(cert=c),
            server=SimpleNamespace(tls_cert=tmp_path / "missing.crt"),
        )
    )
    out = await cert_expiry(ctx(s))
    assert [(x.subject.rsplit("/", 1)[-1], x.severity) for x in out] == [
        ("b.crt", "warning"),
        ("c.crt", "critical"),
    ]
    assert "has expired" in out[1].title and "expired 2 days ago" in out[1].detail
    assert "expires in 20 days" in out[0].detail
    monkeypatch.setattr(
        "mcui.alerts.rules.cert_days", lambda p: (_ for _ in ()).throw(ssl.SSLError())
    )
    assert await cert_expiry(ctx(s)) == []
    s.settings.lxd = None
    s.settings.metrics.cert = None
    assert await cert_expiry(ctx(s)) == []


def test_cert_days(certs):
    assert 1 < cert_days(certs.other_cert) < 3  # the test certificates last two days


async def test_collection():
    s = svc()
    s.collector.status = {
        "ceph": TaskStatus(last_error="refused", last_ok=None),
        "node": TaskStatus(last_error="401", last_ok=NOW - 10),  # recent success: not yet
        "lxd": TaskStatus(),
    }
    [a] = await collection(ctx(s))
    assert a.subject == "ceph" and "refused" in a.detail
    assert await collection(ctx(svc(collector=None))) == []


async def test_evaluate_and_disabled():
    s = svc(lxd=None, collector=None)
    res = await evaluate(ctx(s), {"ceph_health"})
    assert set(res) == set(RULES) and res["ceph_health"] == []

    async def boom(_ctx):
        raise NoData("x")

    rules = dict(RULES)
    rules["ceph_raw"] = (boom, 0)
    import mcui.alerts.rules as r

    old = r.RULES
    r.RULES = rules
    try:
        assert (await evaluate(ctx(s), set()))["ceph_raw"] is None

        # an unexpected error loses only its own rule, logged once until it works again
        async def broken(_ctx):
            raise KeyError("odd shape")

        rules["ceph_raw"] = (broken, 0)
        res = await evaluate(ctx(s), set())
        assert res["ceph_raw"] is None and res["collection"] == [] and "ceph_raw" in r.BROKEN
        rules["ceph_raw"] = RULES["ceph_raw"]
        await evaluate(ctx(s), set())
        assert "ceph_raw" not in r.BROKEN
    finally:
        r.RULES = old


# --- service and API -----------------------------------------------------------------------


async def test_service(tmp_path):
    settings = Settings(
        server={"data_dir": str(tmp_path)}, alerts={"thresholds": {"host_fs": [50, 60]}}
    )
    svc_ = build_services(settings, store=fast_store())
    alerts = svc_.alerts
    assert alerts.thresholds()["host_fs"] == (50.0, 60.0)
    assert alerts.thresholds()["local_pool"] == (80.0, 90.0)
    svc_.store.set_setting("alerts.thresholds", {"host_fs": [70, 80]})
    assert alerts.thresholds()["host_fs"] == (70.0, 80.0)
    assert alerts.disabled() == set()
    svc_.store.set_setting("alerts.disabled", ["collection"])
    assert alerts.disabled() == {"collection"}
    assert await alerts.run_once() == []  # no LXD: every rule finds nothing


async def test_alert_loop(tmp_path, monkeypatch):
    settings = Settings(server={"data_dir": str(tmp_path)}, alerts={"interval_seconds": 10})
    svc_ = build_services(settings, store=fast_store(), clients=lxd_pair())
    calls = []

    async def run_once():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("one bad round")
        return []

    svc_.alerts.run_once = run_once
    svc_.collector = None
    monkeypatch.setattr("mcui.services.HOUSEKEEPING_SECONDS", 0.01)
    real_sleep = asyncio.sleep
    monkeypatch.setattr("mcui.services.asyncio.sleep", lambda s: real_sleep(0.005))
    await svc_.start()
    await real_sleep(0.05)
    await svc_.aclose()
    assert len(calls) >= 2


async def test_switched_off_resolves(tmp_path):
    settings = Settings(server={"data_dir": str(tmp_path)}, alerts={"enabled": False})
    svc_ = build_services(settings, store=fast_store())
    svc_.alerts.store.fire(alert())
    svc_.collector = None
    await svc_.start()
    assert svc_.alerts.store.list(True) == []
    await svc_.aclose()


def seed(services, **kw):
    a = Alert(
        kw.get("rule", "ceph_health"),
        kw.get("subject", "MON_DISK_LOW"),
        "warning",
        "t",
        "d",
        "/ceph",
        kw.get("project"),
    )
    return services.alerts.store.fire(a)


async def test_api_for_cluster_roles(app, services):
    cluster = seed(services)
    web = seed(services, rule="instance_oom", subject="web/c1", project="web")
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert [a["id"] for a in (await c.get("/api/alerts")).json()] == [web, cluster]
        r = await c.post(
            f"/api/alerts/{cluster}/ack", headers={CSRF_HEADER: c.cookies.get("x") or ""}
        )
        assert r.status_code in (403, 404)
        h = await admin(c)
        r = await c.post(f"/api/alerts/{cluster}/ack", headers=h)
        assert r.json()["ack_by"] == "admin"
        assert services.store.audit_entries(limit=1)[0]["action"] == "alert.ack"
        services.alerts.store.resolve(web)
        assert (await c.post(f"/api/alerts/{web}/ack", headers=h)).status_code == 409
        hist = (await c.get("/api/alerts?state=history")).json()
        assert [a["id"] for a in hist] == [web]
        assert (await c.post("/api/alerts/999/ack", headers=h)).status_code == 404


async def test_api_for_project_users(tmp_path):
    store = fast_store()
    store.add_user("pat", "patpw", "project")
    store.set_grant("pat", "web", "admin")
    store.set_grant("pat", "lab", "viewer")
    settings = Settings(server={"data_dir": str(tmp_path), "secure_cookies": False})
    svc_ = build_services(settings, store=store, clients=lxd_pair())
    a = create_app(services=svc_, static_dir=None)
    cluster = seed(svc_)
    web = seed(svc_, rule="instance_oom", subject="web/c1", project="web")
    lab = seed(svc_, rule="instance_oom", subject="lab/c1", project="lab")
    other = seed(svc_, rule="instance_oom", subject="x/c1", project="other")
    async with client(a) as c:
        h = {CSRF_HEADER: await login(c, "pat", "patpw")}
        assert [x["id"] for x in (await c.get("/api/alerts")).json()] == [lab, web]
        assert (await c.post(f"/api/alerts/{web}/ack", headers=h)).status_code == 200
        for i in (lab, cluster, other):  # a viewer there; cluster-wide; another project
            assert (await c.post(f"/api/alerts/{i}/ack", headers=h)).status_code == 404


async def test_alert_settings(app, services):
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get("/api/settings/alerts")).status_code == 403
        h = await admin(c)
        v = (await c.get("/api/settings/alerts")).json()
        assert v["thresholds"]["local_pool"] == [80.0, 90.0] and not v["thresholds_saved"]
        rules = {r["name"]: r for r in v["rules"]}
        assert rules["cert_expiry"]["threshold"] == "cert_days"
        assert rules["ceph_health"]["threshold"] is None
        r = await c.put(
            "/api/settings/alerts", json={"thresholds": {"local_pool": [70, 85]}}, headers=h
        )
        assert r.json()["thresholds"]["local_pool"] == [70.0, 85.0] and r.json()["thresholds_saved"]
        for bad in (
            {"thresholds": {"nope": [1, 2]}},
            {"thresholds": {"local_pool": [90, 80]}},
            {"thresholds": {"cert_days": [7, 30]}},
            {"thresholds": {"host_fs": [0, 0]}},  # an emptied field: everything alerts
            {"thresholds": {"host_fs": [-1, 50]}},
            {"thresholds": {"host_fs": [50, 150]}},
            {"thresholds": {"cert_days": [30, 0]}},
            {"disabled": ["nope"]},
        ):
            assert (await c.put("/api/settings/alerts", json=bad, headers=h)).status_code == 422, (
                bad
            )
        r = await c.put(
            "/api/settings/alerts", json={"disabled": ["collection", "collection"]}, headers=h
        )
        assert r.json()["disabled"] == ["collection"] and r.json()["disabled_saved"]
        r = await c.put(
            "/api/settings/alerts",
            json={"reset_thresholds": True, "reset_disabled": True},
            headers=h,
        )
        assert not r.json()["thresholds_saved"] and r.json()["disabled"] == []
        assert services.store.audit_entries(limit=1)[0]["action"] == "settings.alerts"


def test_config_checks():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="unknown alert thresholds"):
        Settings(alerts={"thresholds": {"nope": [1, 2]}})
    with pytest.raises(ValidationError, match="unknown alert rules"):
        Settings(alerts={"disabled": ["nope"]})
    for bad in ([90, 80], [0, 50], [float("nan"), 90], [50, float("inf")]):
        with pytest.raises(ValidationError, match="alerts threshold"):
            Settings(alerts={"thresholds": {"host_fs": bad}})
    with pytest.raises(ValidationError, match="days left"):
        Settings(alerts={"thresholds": {"cert_days": [7, 30]}})
