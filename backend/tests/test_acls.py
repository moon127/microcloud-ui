import pytest
import respx

from mcui.config import Settings
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import EP1, fast_store, lxd_err, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair
from tests.test_manage import admin, audit, body, params

ACLS = f"{EP1}/1.0/network-acls"
FWD = f"{EP1}/1.0/networks/br0/forwards"


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


@respx.mock
async def test_acls(app, services):
    lst = respx.get(ACLS).mock(return_value=lxd_ok([{"name": "web"}]))
    get = respx.get(f"{ACLS}/web").mock(return_value=lxd_ok({"name": "web", "ingress": []}))
    create = respx.post(ACLS).mock(return_value=lxd_ok(None))
    put = respx.put(f"{ACLS}/web").mock(return_value=lxd_ok(None))
    ren = respx.post(f"{ACLS}/web").mock(return_value=lxd_ok(None))
    dele = respx.delete(f"{ACLS}/web").mock(return_value=lxd_err("ACL is in use", 400))
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "viewer", "viewerpw")}
        assert (await c.get("/api/network-acls", params={"project": "p"})).json() == [
            {"name": "web"}
        ]
        assert params(lst) == {"recursion": "1", "project": "p"}
        assert (await c.get("/api/network-acls/p/web")).json()["name"] == "web"
        assert params(get) == {"project": "p"}
        assert (await c.post("/api/network-acls", json={"name": "x"}, headers=h)).status_code == 403
        h = await admin(c)
        rule = {"action": "allow", "protocol": "tcp", "destination_port": "22"}
        await c.post(
            "/api/network-acls",
            json={"name": "web", "project": "p", "ingress": [rule]},
            headers=h,
        )
        assert body(create) == {
            "name": "web",
            "description": "",
            "ingress": [
                {
                    "action": "allow",
                    "state": "enabled",
                    "description": "",
                    "source": "",
                    "destination": "",
                    "protocol": "tcp",
                    "source_port": "",
                    "destination_port": "22",
                    "icmp_type": "",
                    "icmp_code": "",
                }
            ],
            "egress": [],
            "config": {},
        }
        assert params(create) == {"project": "p"}
        bad = await c.post(
            "/api/network-acls",
            json={"name": "web", "ingress": [{"action": "permit"}]},
            headers=h,
        )
        assert bad.status_code == 422
        await c.put(
            "/api/network-acls/p/web",
            json={"description": "d", "egress": [{"action": "drop", "state": "logged"}]},
            headers=h,
        )
        assert body(put)["egress"][0]["state"] == "logged" and body(put)["description"] == "d"
        await c.post("/api/network-acls/p/web/rename", json={"name": "web2"}, headers=h)
        assert body(ren) == {"name": "web2"}
        r = await c.delete("/api/network-acls/p/web", headers=h)
        assert r.status_code == 502 and "in use" in r.text
    assert dele.call_count == 1
    assert [a[0] for a in audit(services, 4)] == [
        "network-acl.delete",
        "network-acl.rename",
        "network-acl.update",
        "network-acl.create",
    ]


@respx.mock
async def test_forwards(app, services):
    lst = respx.get(FWD).mock(return_value=lxd_ok([{"listen_address": "192.0.2.10"}]))
    create = respx.post(FWD).mock(return_value=lxd_ok(None))
    put = respx.put(f"{FWD}/192.0.2.10").mock(return_value=lxd_ok(None))
    dele = respx.delete(f"{FWD}/192.0.2.10").mock(return_value=lxd_ok(None))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get("/api/networks/default/br0/forwards")).json()[0]["listen_address"]
        assert params(lst) == {"recursion": "1", "project": "default"}
        h = await admin(c)
        port = {"protocol": "tcp", "listen_port": "80,443", "target_address": "10.0.0.5"}
        r = await c.post(
            "/api/networks/default/br0/forwards",
            params={"target": "m1"},
            json={
                "listen_address": "192.0.2.10",
                "config": {"target_address": "10.0.0.6"},
                "ports": [port],
            },
            headers=h,
        )
        assert r.status_code == 200, r.text
        assert params(create) == {"project": "default", "target": "m1"}
        assert body(create)["ports"] == [{**port, "target_port": "", "description": ""}]
        for bad in (
            {"listen_address": "not-an-ip"},
            {"listen_address": "192.0.2.10", "ports": [{**port, "target_address": "x"}]},
            {"listen_address": "192.0.2.10", "ports": [{**port, "listen_port": "http"}]},
            {"listen_address": "192.0.2.10", "ports": [{**port, "protocol": "sctp"}]},
        ):
            r = await c.post("/api/networks/default/br0/forwards", json=bad, headers=h)
            assert r.status_code == 422, bad
        await c.put(
            "/api/networks/default/br0/forwards/192.0.2.10",
            json={"description": "web", "ports": []},
            headers=h,
        )
        assert body(put) == {"description": "web", "config": {}, "ports": []}
        assert params(put) == {"project": "default"}
        await c.delete("/api/networks/default/br0/forwards/192.0.2.10", headers=h)
        assert dele.call_count == 1
        r = await c.delete("/api/networks/default/br0/forwards/nope", headers=h)
        assert r.status_code == 422
        r = await c.put("/api/networks/default/br0/forwards/nope", json={}, headers=h)
        assert r.status_code == 422
    assert audit(services, 1)[0][:3] == (
        "network-forward.delete",
        "default/br0/192.0.2.10",
        True,
    )
