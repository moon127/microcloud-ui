import time
from pathlib import Path

import pytest
import respx

from mcui.config import Settings
from mcui.main import create_app
from mcui.metrics.ceph import CephScrape
from mcui.metrics.ceph_view import rbd_owner, summarize
from mcui.metrics.prom import parse
from mcui.metrics.store import MetricStore, Sample, SQLiteMetricStore
from mcui.services import build_services
from tests.conftest import EP1, fast_store, lxd_err, lxd_ok
from tests.test_api import client, login, lxd_pair

FIXTURE = Path(__file__).parent / "fixtures" / "recorded" / "ceph_mgr_active_perf_rbd_on.txt"


def test_summary_from_a_real_mgr():
    s = summarize(parse(FIXTURE.read_text()))
    assert s["health"] == {
        "status": "HEALTH_WARN",
        "checks": [{"name": "MON_DISK_LOW", "severity": "HEALTH_WARN"}],
    }
    assert s["version"] == "19.2.3 squid"
    assert [m["in_quorum"] for m in s["mons"]] == [True, True, True]
    assert [o["id"] for o in s["osds"]] == [1, 2, 3, 4, 6, 9]
    osd = s["osds"][0]
    assert osd["host"] == "gw-ips-microcloud-3" and osd["device_class"] == "ssd"
    assert osd["up"] is True and osd["in"] is True
    assert "apply_latency_ms" in osd and osd["size"] > osd["used"] > 0
    assert s["pgs"]["total"] == 193
    assert s["pgs"]["states"]["active"] == 193 and "degraded" not in s["pgs"]["states"]
    assert s["capacity"]["total"] > s["capacity"]["used_raw"] > 0
    lxd = next(p for p in s["pools"] if p["name"] == "lxd_remote")
    assert lxd["type"] == "replicated" and lxd["pgs"] == 128 and lxd["stored"] > 0


def test_summary_of_nothing():
    s = summarize(parse(""))
    assert s["health"]["status"] == "UNKNOWN" and s["osds"] == [] and s["pools"] == []
    assert s["version"] is None
    s = summarize(
        parse(
            "ceph_health_status 2\n"
            'ceph_osd_metadata{ceph_daemon="osd.0",ceph_version="weird"} 1\n'
            'ceph_osd_up{ceph_daemon="mds.a"} 1\n'
        )
    )
    assert s["health"]["status"] == "HEALTH_ERR" and s["version"] == "weird"
    assert s["osds"] == [{"id": 0, "host": "", "device_class": ""}]


@pytest.mark.parametrize(
    ("image", "owner"),
    [
        (
            "container_web",
            {
                "kind": "instance",
                "type": "container",
                "project": "default",
                "name": "web",
                "block": False,
            },
        ),
        (
            "virtual-machine_lab_vm1.block",
            {
                "kind": "instance",
                "type": "virtual-machine",
                "project": "lab",
                "name": "vm1",
                "block": True,
            },
        ),
        (
            "custom_lab-2_my_data",
            {
                "kind": "volume",
                "type": "custom",
                "project": "lab-2",
                "name": "my_data",
                "block": False,
            },
        ),
        (
            "custom_data_x",
            {
                "kind": "volume",
                "type": "custom",
                "project": "default",
                "name": "data_x",
                "block": False,
            },
        ),
        ("image_abc123_ext4.block", {"kind": "image", "name": "abc123", "block": True}),
        (
            "zombie_container_old",
            {
                "kind": "instance",
                "type": "container",
                "project": "default",
                "name": "old",
                "block": False,
                "deleted": True,
            },
        ),
        ("lxd_lxd_remote", {"kind": "other"}),
    ],
)
def test_rbd_owner(image, owner):
    assert rbd_owner(image, ["default", "lab", "lab-2"]) == owner


NOW = int(time.time())
T = NOW - NOW % 60 - 30


def rbd(image, name, value):
    return Sample(
        f"ceph_rbd_{name}",
        {"pool": "lxd_remote", "namespace": "", "image": image},
        value,
        "counter",
    )


@pytest.fixture
async def services(tmp_path):
    store = fast_store()
    store.add_user("viewer", "viewerpw", "viewer")
    settings = Settings(server={"data_dir": str(tmp_path), "secure_cookies": False})
    ms = MetricStore(SQLiteMetricStore(":memory:"))
    svc = build_services(settings, store=store, clients=lxd_pair(), metrics_store=ms)
    for i, t in enumerate((T - 15, T)):
        await ms.write(
            t,
            [
                rbd("container_web", "write_bytes", 1500 * i),  # 100 B/s
                rbd("container_lab_db", "write_bytes", 15000 * i),  # 1000 B/s
                rbd("container_web", "write_latency_sum", 30e6 * i),  # 30 ms over…
                rbd("container_web", "write_latency_count", 3 * i),  # …3 writes = 10 ms each
                rbd("container_idle", "write_latency_sum", 0),
                rbd("container_idle", "write_latency_count", 0),
            ],
        )
    return svc


@pytest.fixture
def app(services):
    return create_app(services=services, static_dir=None)


@respx.mock
async def test_rbd_top(app):
    projects = respx.get(f"{EP1}/1.0/projects").mock(
        return_value=lxd_ok(["/1.0/projects/default", "/1.0/projects/lab"])
    )
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        r = (await c.get("/api/ceph/rbd", params={"range": 300})).json()
        assert r["metric"] == "write_bytes" and r["range"] == 300
        assert [
            (x["image"], round(x["value"]), x["owner"]["project"], x["owner"]["name"])
            for x in r["rows"]
        ] == [
            ("container_lab_db", 1000, "lab", "db"),
            ("container_web", 100, "default", "web"),
        ]
        r = (await c.get("/api/ceph/rbd", params={"metric": "write_latency", "range": 300})).json()
        assert [(x["image"], round(x["value"], 3)) for x in r["rows"]] == [("container_web", 10.0)]
        assert (await c.get("/api/ceph/rbd", params={"metric": "bogus"})).status_code == 422
        projects.mock(return_value=lxd_err("forbidden", 403))
        r = (await c.get("/api/ceph/rbd", params={"range": 300, "limit": 1})).json()
        assert r["rows"][0]["owner"] == {
            "kind": "instance",
            "type": "container",
            "project": "default",
            "name": "lab_db",
            "block": False,
        }


async def test_state(app, services):
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get("/api/ceph")).json() == {
            "enabled": True,
            "ok": False,
            "error": "not scraped yet",
        }
        services.collector.ceph_last = CephScrape(False, error="no active ceph-mgr found")
        services.collector.ceph_at = 100.0
        assert (await c.get("/api/ceph")).json() == {
            "enabled": True,
            "ok": False,
            "error": "no active ceph-mgr found",
            "scraped_at": 100.0,
        }
        services.collector.ceph_last = CephScrape(
            True, "http://m1:9283", exposition=parse(FIXTURE.read_text())
        )
        body = (await c.get("/api/ceph")).json()
        assert body["ok"] is True and body["mgr"] == "http://m1:9283"
        assert body["health"]["status"] == "HEALTH_WARN" and len(body["osds"]) == 6
        services.ceph = None
        assert (await c.get("/api/ceph")).json() == {"enabled": False}


RECORDED = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("recorded-5.21/ceph_mgr.txt", True),  # Squid exports OSD perf counters
        ("recorded-6.10/ceph_mgr_tentacle_defaults.txt", False),  # Tentacle leaves them out
        ("recorded-6.10/ceph_mgr.txt", True),  # exclude_perf_counters false + mgr restart
        ("recorded-6.10-cluster/ceph_mgr.txt", True),  # D23 setting kept through the upgrade
    ],
)
def test_perf_counters_on_real_scrapes(fixture, expected):
    """OSD size and per-op latency need perf counters; say when they're missing (Phase 5)."""
    s = summarize(parse((RECORDED / fixture).read_text()))
    assert s["perf_counters"] is expected
    assert s["osds"] and all("apply_latency_ms" in o for o in s["osds"])  # always exported
    assert all(("size" in o) is expected for o in s["osds"])


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        # Tentacle exports the cluster's own ratios (0.85/0.95 as float32)
        ("recorded-6.10-cluster/ceph_mgr.txt", {"nearfull": 0.85, "full": 0.95, "default": False}),
        # Squid doesn't: Ceph's defaults, marked as such
        ("recorded-5.21/ceph_mgr.txt", {"nearfull": 0.85, "full": 0.95, "default": True}),
    ],
)
def test_full_ratios_on_real_scrapes(fixture, expected):
    assert summarize(parse((RECORDED / fixture).read_text()))["full_ratios"] == expected


def test_full_ratios_set_by_the_cluster():
    exp = parse("ceph_osd_nearfull_ratio 0.75\nceph_osd_full_ratio 0.9\n")
    assert summarize(exp)["full_ratios"] == {"nearfull": 0.75, "full": 0.9, "default": False}


def test_perf_counters_without_osds():
    assert summarize(parse("ceph_health_status 0\n"))["perf_counters"] is True
