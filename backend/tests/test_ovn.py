import pytest
import respx

from mcui.api.ovn import in_ranges, range_size
from mcui.config import Settings
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import EP1, fast_store, lxd_err, lxd_ok
from tests.test_api import client, login, lxd_pair
from tests.test_manage import params


def test_ranges():
    assert range_size("192.0.2.50-192.0.2.99") == 50
    assert range_size("192.0.2.1-192.0.2.2, 192.0.2.10") == 3
    assert range_size("nonsense") == 0
    assert in_ranges("192.0.2.60", "192.0.2.50-192.0.2.99")
    assert in_ranges("192.0.2.10/32", "192.0.2.1-192.0.2.2,192.0.2.10")
    assert not in_ranges("192.0.2.100", "192.0.2.50-192.0.2.99")
    assert not in_ranges("2001:db8::1", "192.0.2.50-192.0.2.99")
    assert not in_ranges("x", "192.0.2.50-192.0.2.99")


@pytest.fixture
def app(tmp_path):
    store = fast_store()
    store.add_user("viewer", "viewerpw", "viewer")
    settings = Settings(server={"data_dir": str(tmp_path), "secure_cookies": False})
    return create_app(
        services=build_services(settings, store=store, clients=lxd_pair()), static_dir=None
    )


NET = f"{EP1}/1.0/networks"


@respx.mock
async def test_overview(app):
    respx.get(f"{EP1}/1.0/projects").mock(
        return_value=lxd_ok(
            [
                {"name": "default", "config": {}},
                {"name": "lab", "config": {"features.networks": "true"}},
                {"name": "borrower", "config": {"features.networks": "false"}},
                {"name": "broken", "config": {"features.networks": "true"}},
            ]
        )
    )
    respx.get(NET, params={"project": "default"}).mock(
        return_value=lxd_ok(
            [
                {
                    "name": "o1",
                    "type": "ovn",
                    "status": "Created",
                    "used_by": ["/1.0/instances/a"],
                    "config": {
                        "network": "UPLINK",
                        "ipv4.address": "10.0.0.1/24",
                        "ipv4.nat": "true",
                        "volatile.network.ipv4.address": "192.0.2.50",
                        "security.acls": "web,ssh",
                    },
                },
                {
                    "name": "UPLINK",
                    "type": "physical",
                    "managed": True,
                    "config": {
                        "ipv4.gateway": "192.0.2.1/24",
                        "ipv4.ovn.ranges": "192.0.2.50-192.0.2.59",
                        "bgp.peers.r1.address": "192.0.2.254",
                        "bgp.peers.r1.asn": "65000",
                    },
                },
                {"name": "eno1", "type": "physical", "managed": False},
                {"name": "br0", "type": "bridge", "managed": True},
            ]
        )
    )
    respx.get(NET, params={"project": "lab"}).mock(
        return_value=lxd_ok([{"name": "o2", "type": "ovn", "config": {"network": "UPLINK"}}])
    )
    respx.get(NET, params={"project": "broken"}).mock(return_value=lxd_err("boom", 500))
    respx.get(f"{NET}/o1/state").mock(return_value=lxd_ok({"ovn": {"chassis": "m2"}}))
    respx.get(f"{NET}/o1/forwards").mock(return_value=lxd_ok([{"listen_address": "192.0.2.10"}]))
    respx.get(f"{NET}/o1/load-balancers").mock(return_value=lxd_ok(None))
    respx.get(f"{NET}/o1/peers").mock(return_value=lxd_ok([]))
    st = respx.get(f"{NET}/o2/state").mock(return_value=lxd_err("Network not found", 404))
    respx.get(f"{NET}/o2/forwards").mock(return_value=lxd_ok([]))
    respx.get(f"{NET}/o2/load-balancers").mock(return_value=lxd_err("not supported", 400))
    respx.get(f"{NET}/o2/peers").mock(return_value=lxd_ok([]))
    alloc = respx.get(f"{EP1}/1.0/network-allocations").mock(
        return_value=lxd_ok(
            [
                {"network": "o1", "type": "instance"},
                {"network": "o1", "type": "instance"},
                {"network": "o1", "type": "network-forward"},
            ]
        )
    )
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        body = (await c.get("/api/ovn")).json()
    assert params(st) == {"project": "lab"}
    o1, o2 = body["networks"]
    assert o1 == {
        "name": "o1",
        "project": "default",
        "description": "",
        "status": "Created",
        "uplink": "UPLINK",
        "ipv4": "10.0.0.1/24",
        "ipv6": "",
        "ipv4_nat": True,
        "ipv6_nat": False,
        "router_ipv4": "192.0.2.50",
        "router_ipv6": "",
        "acls": ["web", "ssh"],
        "used_by": 1,
        "chassis": "m2",
        "state_error": None,
        "forwards": [{"listen_address": "192.0.2.10"}],
        "load_balancers": [],
        "peers": [],
        "allocations": {"instance": 2, "network-forward": 1},
    }
    assert o2["project"] == "lab" and o2["chassis"] is None
    assert o2["state_error"] == "Network not found" and o2["load_balancers"] is None
    assert o2["allocations"] == {}
    [up] = body["uplinks"]
    assert up["bgp_peers"] == ["r1"] and up["networks"] == ["o1", "o2"]
    assert (up["ipv4_used"], up["ipv4_size"]) == (1, 10)
    assert params(alloc) == {"all-projects": "true"}


@respx.mock
async def test_overview_without_allocations(app):
    respx.get(f"{EP1}/1.0/projects").mock(return_value=lxd_ok([{"name": "default"}]))
    respx.get(NET).mock(
        return_value=lxd_ok([{"name": "UP", "type": "physical", "managed": True, "config": {}}])
    )
    respx.get(f"{EP1}/1.0/network-allocations").mock(return_value=lxd_err("forbidden", 403))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        body = (await c.get("/api/ovn")).json()
    assert body["networks"] == []
    assert body["uplinks"][0]["ipv4_size"] == 0 and body["uplinks"][0]["networks"] == []
