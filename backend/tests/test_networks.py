import json

import pytest
import respx

from mcui.config import Settings
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import EP1, fast_store, fixture_json, lxd_err, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair
from tests.test_manage import admin, audit, body, params

NETS = f"{EP1}/1.0/networks"


@pytest.fixture
def services(tmp_path):
    store = fast_store()
    store.add_user("admin", "adminpw", "admin")
    store.add_user("viewer", "viewerpw", "viewer")
    settings = Settings(server={"data_dir": str(tmp_path), "secure_cookies": False})
    return build_services(settings, store=store, clients=lxd_pair())


@pytest.fixture
def app(services):
    return create_app(services=services, static_dir=None)


def clustered():
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(fixture_json("server_clustered.json")))
    respx.get(f"{EP1}/1.0/cluster/members", params={"recursion": "1"}).mock(
        return_value=lxd_ok(fixture_json("cluster_members.json"))
    )


def standalone():
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(fixture_json("server_standalone.json")))


@respx.mock
async def test_detail_state_leases_allocations(app):
    get = respx.get(f"{NETS}/br0").mock(return_value=lxd_ok({"name": "br0", "config": {}}))
    state = respx.get(f"{NETS}/br0/state").mock(return_value=lxd_ok({"state": "up"}))
    respx.get(f"{NETS}/br0/leases").mock(return_value=lxd_ok([{"address": "10.0.0.2"}]))
    alloc = respx.get(f"{EP1}/1.0/network-allocations").mock(return_value=lxd_ok(None))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get("/api/networks/default/br0")).json()["name"] == "br0"
        assert params(get) == {"project": "default"}
        await c.get("/api/networks/default/br0", params={"target": "m1"})
        assert params(get) == {"project": "default", "target": "m1"}
        assert (await c.get("/api/networks/p/br0/state")).json() == {"state": "up"}
        await c.get("/api/networks/p/br0/state", params={"target": "m2"})
        assert params(state) == {"project": "p", "target": "m2"}
        assert (await c.get("/api/networks/p/br0/leases")).json() == [{"address": "10.0.0.2"}]
        assert (await c.get("/api/network-allocations")).json() == []
        assert params(alloc) == {"all-projects": "true"}
        await c.get("/api/network-allocations", params={"project": "p"})
        assert params(alloc) == {"project": "p"}
        r = await c.get("/api/networks/default/br0", params={"target": "bad name"})
        assert r.status_code == 422


@respx.mock
async def test_member_settings(app):
    clustered()
    respx.get(f"{NETS}/up", params={"target": "m1"}).mock(
        return_value=lxd_ok({"config": {"parent": "eth1", "ipv4.gateway": "10.0.0.1/24"}})
    )
    respx.get(f"{NETS}/up", params={"target": "m2"}).mock(
        return_value=lxd_err("Member is offline", 503)
    )
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get("/api/networks/default/up/members")).json() == [
            {"member": "m1", "config": {"parent": "eth1"}},
            {"member": "m2", "config": {}, "error": "Member is offline"},
        ]


@respx.mock
async def test_member_settings_standalone(app):
    standalone()
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get("/api/networks/default/up/members")).json() == []


@respx.mock
async def test_create_bridge_in_a_cluster(app, services):
    clustered()
    post = respx.post(NETS).mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "viewer", "viewerpw")}
        new = {"name": "br1", "type": "bridge", "config": {"ipv4.address": "auto"}}
        assert (await c.post("/api/networks", json=new, headers=h)).status_code == 403
        h = await admin(c)
        r = await c.post(
            "/api/networks",
            json={**new, "members": {"m1": {"bridge.external_interfaces": "eth2"}}},
            headers=h,
        )
        assert r.status_code == 200, r.text
    sent = [(dict(c.request.url.params), c.request.content) for c in post.calls]
    assert [(p, json.loads(b)) for p, b in sent] == [
        (
            {"project": "default", "target": "m1"},
            {"name": "br1", "type": "bridge", "config": {"bridge.external_interfaces": "eth2"}},
        ),
        ({"project": "default", "target": "m2"}, {"name": "br1", "type": "bridge", "config": {}}),
        (
            {"project": "default"},
            {
                "name": "br1",
                "type": "bridge",
                "description": "",
                "config": {"ipv4.address": "auto"},
            },
        ),
    ]
    assert audit(services) == [
        (
            "network.create",
            "default/br1",
            True,
            {"type": "bridge", "members": {"m1": {"bridge.external_interfaces": "eth2"}}},
        )
    ]


@respx.mock
async def test_create_ovn_skips_pending(app):
    clustered()
    post = respx.post(NETS).mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = await admin(c)
        await c.post(
            "/api/networks",
            json={"name": "o1", "type": "ovn", "project": "p", "config": {"network": "UPLINK"}},
            headers=h,
        )
        assert post.call_count == 1 and params(post) == {"project": "p"}


@respx.mock
async def test_create_standalone_skips_pending(app):
    standalone()
    post = respx.post(NETS).mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = await admin(c)
        await c.post(
            "/api/networks",
            json={"name": "mv", "type": "macvlan", "members": {"lxd1": {"parent": "eth0"}}},
            headers=h,
        )
        assert post.call_count == 1 and body(post)["config"] == {"parent": "eth0"}


@respx.mock
async def test_create_failure_forgets_pending(app, services):
    clustered()
    respx.post(NETS, params={"target": "m1"}).mock(return_value=lxd_ok(None))
    respx.post(NETS, params={"target": "m2"}).mock(
        return_value=lxd_err("Parent interface not found", 400)
    )
    dele = respx.delete(f"{NETS}/p1").mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = await admin(c)
        r = await c.post(
            "/api/networks",
            json={"name": "p1", "type": "physical", "members": {"m2": {"parent": "nope"}}},
            headers=h,
        )
        assert r.status_code == 502 and "Parent interface not found" in r.text
    assert dele.call_count == 1
    assert audit(services)[0][2] is False


@respx.mock
async def test_create_final_step_failure_forgets_pending(app):
    clustered()
    respx.post(NETS, params={"target": "m1"}).mock(return_value=lxd_ok(None))
    respx.post(NETS, params={"target": "m2"}).mock(return_value=lxd_ok(None))
    respx.post(NETS, params={"project": "default"}).mock(
        return_value=lxd_err("Invalid option", 400)
    )
    dele = respx.delete(f"{NETS}/m").mock(return_value=lxd_err("not found", 404))
    async with client(app) as c:
        h = await admin(c)
        r = await c.post("/api/networks", json={"name": "m", "type": "macvlan"}, headers=h)
        assert r.status_code == 502
    assert dele.call_count == 1


@respx.mock
async def test_create_final_step_failure_ovn(app):
    clustered()
    post = respx.post(NETS).mock(return_value=lxd_err("Uplink not found", 400))
    dele = respx.delete(f"{NETS}/o").mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = await admin(c)
        r = await c.post("/api/networks", json={"name": "o", "type": "ovn"}, headers=h)
        assert r.status_code == 502
    assert post.call_count == 1 and dele.call_count == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "x", "type": "bridge", "members": {"m1": {"ipv4.address": "auto"}}},
        {"name": "much-too-long-name", "type": "bridge"},
        {"name": "x", "type": "sriov"},
    ],
)
@respx.mock
async def test_create_validation(app, payload):
    async with client(app) as c:
        h = await admin(c)
        r = await c.post("/api/networks", json=payload, headers=h)
        assert r.status_code == 422


@respx.mock
async def test_update_and_delete(app, services):
    put = respx.put(f"{NETS}/br0").mock(return_value=lxd_ok(None))
    respx.get(f"{NETS}/br0").mock(
        return_value=lxd_ok(
            {"description": "keep me", "config": {"volatile.network.ipv4.address": "x"}}
        )
    )
    respx.delete(f"{NETS}/br0").mock(return_value=lxd_err("Network is in use", 400))
    async with client(app) as c:
        h = await admin(c)
        await c.put(
            "/api/networks/default/br0",
            json={"description": "d", "config": {"ipv4.nat": "true"}},
            headers=h,
        )
        assert body(put) == {
            "description": "d",
            "config": {"ipv4.nat": "true", "volatile.network.ipv4.address": "x"},
        }
        assert params(put) == {"project": "default"}
        await c.put(
            "/api/networks/default/br0",
            params={"target": "m1"},
            json={"config": {"bridge.external_interfaces": "eth3"}},
            headers=h,
        )
        assert params(put) == {"project": "default", "target": "m1"}
        assert body(put) == {
            "description": "keep me",
            "config": {"bridge.external_interfaces": "eth3"},
        }
        r = await c.put(
            "/api/networks/default/br0",
            params={"target": "m1"},
            json={"config": {"ipv4.nat": "true"}},
            headers=h,
        )
        assert r.status_code == 422
        r = await c.delete("/api/networks/default/br0", headers=h)
        assert r.status_code == 502 and "in use" in r.text
    assert [a[0] for a in audit(services, 3)] == [
        "network.delete",
        "network.update",
        "network.update",
    ]
    assert audit(services, 2)[1][3] == {"target": "m1"}
