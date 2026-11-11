import time

import httpx
import pytest
import respx

from mcui.api.hosts import allocation, cpu_count, parse_size
from mcui.config import Settings
from mcui.main import create_app
from mcui.metrics.store import Sample
from mcui.services import build_services
from tests.conftest import EP1, fast_store, fixture_json, fixture_text, lxd_err, lxd_ok
from tests.test_api import client, login, lxd_pair
from tests.test_node import FIXTURE, later

GIB = 1024**3


def test_sizes_and_cpus():
    assert parse_size("2GiB") == 2 * GIB
    assert parse_size("512MB") == 512e6
    assert parse_size("1.5 gib") == 1.5 * GIB
    assert parse_size("1073741824") == GIB
    assert parse_size("lots") is None and parse_size("2XB") is None
    assert cpu_count("4") == 4
    assert cpu_count("0-3,8") == 5
    assert cpu_count("0-x") is None and cpu_count("a") is None and cpu_count(",") is None


def test_allocation():
    vm = {"type": "virtual-machine", "expanded_config": {}}
    assert allocation(vm, 16 * GIB) == (1, GIB)  # LXD's VM defaults
    ct = {"type": "container", "config": {"limits.cpu": "2", "limits.memory": "25%"}}
    assert allocation(ct, 16 * GIB) == (2, 4 * GIB)
    assert allocation(ct, None) == (2, None)
    assert allocation({"type": "container"}, 16 * GIB) == (None, None)
    bad = {"type": "container", "config": {"limits.memory": "x%"}}
    assert allocation(bad, 16 * GIB) == (None, None)


def _state(free_gib: int):
    return {
        "sysinfo": {
            "logical_cpus": 8,
            "load_averages": [1.5, 1.0, 0.5],
            "uptime": 3600,
            "total_ram": 32 * GIB,
            "free_ram": free_gib * GIB,
            "buffered_ram": GIB,
            "total_swap": 0,
            "free_swap": 0,
        }
    }


INSTANCES = [
    {"name": "c1", "location": "m1", "status": "Running", "type": "container",
     "expanded_config": {"limits.cpu": "2", "limits.memory": "4GiB"}},
    {"name": "v1", "location": "m1", "status": "Running", "type": "virtual-machine",
     "expanded_config": {}},
    {"name": "c3", "location": "m1", "status": "Running", "type": "container",
     "expanded_config": {}},
    {"name": "off", "location": "m1", "status": "Stopped", "type": "container",
     "expanded_config": {"limits.cpu": "16"}},
    {"name": "c2", "location": "m2", "status": "Running", "type": "container",
     "expanded_config": {}},
]  # fmt: skip


@pytest.fixture
def services(tmp_path):
    store = fast_store()
    store.add_user("viewer", "viewerpw", "viewer")
    settings = Settings(server={"data_dir": str(tmp_path), "secure_cookies": False})
    return build_services(settings, store=store, clients=lxd_pair())


def mock_lxd(m2_state=None):
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(fixture_json("server_clustered.json")))
    respx.get(f"{EP1}/1.0/cluster/members", params={"recursion": "1"}).mock(
        return_value=lxd_ok(fixture_json("cluster_members.json"))
    )
    respx.get(f"{EP1}/1.0/cluster/members/m1/state").mock(return_value=lxd_ok(_state(8)))
    respx.get(f"{EP1}/1.0/cluster/members/m2/state").mock(
        return_value=m2_state or lxd_ok(_state(16))
    )
    respx.get(f"{EP1}/1.0/instances", params={"recursion": "1"}).mock(
        return_value=lxd_ok(INSTANCES)
    )
    respx.get("https://192.0.2.1:8443/1.0/metrics").mock(
        side_effect=[
            httpx.Response(200, text=fixture_text("metrics_m1_t0.txt")),
            httpx.Response(200, text=fixture_text("metrics_m1_t15.txt")),
        ]
    )
    respx.get("https://192.0.2.2:8443/1.0/metrics").mock(
        return_value=httpx.Response(200, text=fixture_text("metrics_m2_t0.txt"))
    )


@respx.mock
async def test_hosts(services):
    mock_lxd(m2_state=lxd_err("member offline", 503))
    text = FIXTURE.read_text()
    respx.get("https://192.0.2.1:9100/metrics").mock(
        side_effect=[httpx.Response(200, text=text), httpx.Response(200, text=later(text))]
    )
    respx.get("https://192.0.2.2:9100/metrics").mock(side_effect=httpx.ConnectError("refused"))
    await services.metrics.write(
        int(time.time()) - 60,
        [
            Sample(
                "mcui_host_network_receive_bytes_total",
                {"member": "m1", "network": "eno1"},
                0,
                "counter",
            )
        ],
    )
    await services.metrics.write(
        int(time.time()) - 30,
        [
            Sample(
                "mcui_host_network_receive_bytes_total",
                {"member": "m1", "network": "eno1"},
                3000,
                "counter",
            )
        ],
    )
    services.live.min_age = 0
    app = create_app(services=services, static_dir=None)
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        r = await c.get("/api/hosts")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["node_exporter"] is True
        m1, m2 = body["hosts"]
        assert m1["name"] == "m1" and m1["cpus"] == 8 and m1["load1"] == 1.5 and m1["load5"] == 1.0
        assert m1["memory_total"] == 32 * GIB and m1["memory_free"] == 8 * GIB
        assert m1["instances"] == 4 and m1["instances_running"] == 3
        assert m1["allocated_cpus"] == 3  # c1 2 + v1 default 1; c3 unlimited, "off" stopped
        assert m1["allocated_memory"] == 5 * GIB
        assert m1["unlimited"] == 1
        assert m1["net_rx_bps"] == pytest.approx(100) and m1["net_tx_bps"] is None
        assert m1["instances_cpu"] == 0 and m1["instances_memory"] > 0
        assert m1["node"]["ok"] and m1["node"]["memory_used"] == 16 * GIB
        assert m2["cpus"] is None and m2["load1"] is None and m2["instances"] == 1
        assert m2["node"]["absent"] is True
        r = await c.get("/api/hosts")  # second round: rates
        m1 = r.json()["hosts"][0]
        assert m1["instances_cpu"] > 0
        assert m1["node"]["cpu_percent"] > 0


@respx.mock
async def test_hosts_standalone_and_without_node_exporter(tmp_path):
    store = fast_store()
    store.add_user("viewer", "viewerpw", "viewer")
    settings = Settings(
        server={"data_dir": str(tmp_path), "secure_cookies": False},
        node_exporter={"enabled": False},
    )
    services = build_services(settings, store=store, clients=lxd_pair())
    assert services.node is None and services.collector.node is None
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(fixture_json("server_standalone.json")))
    respx.get(f"{EP1}/1.0/instances", params={"recursion": "1"}).mock(
        return_value=lxd_ok([{"name": "c1", "status": "Running", "type": "container"}])
    )
    respx.get(f"{EP1}/1.0/metrics").mock(return_value=httpx.Response(200, text=""))
    respx.get(url__regex=r".*/1\.0/cluster/members/.*/state").mock(
        return_value=lxd_err("not clustered", 404)
    )
    app = create_app(services=services, static_dir=None)
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        body = (await c.get("/api/hosts")).json()
    assert body["node_exporter"] is False
    (h,) = body["hosts"]
    assert h["instances"] == 1 and h["node"] is None and h["cpus"] is None
    assert h["load1"] is None


async def test_hosts_without_lxd(tmp_path):
    store = fast_store()
    store.add_user("viewer", "viewerpw", "viewer")
    settings = Settings(server={"data_dir": str(tmp_path), "secure_cookies": False})
    app = create_app(services=build_services(settings, store=store), static_dir=None)
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get("/api/hosts")).status_code == 503
