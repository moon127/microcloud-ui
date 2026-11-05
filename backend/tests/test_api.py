import ssl

import httpx
import pytest
import respx

from mcui.api.deps import COOKIE, CSRF_HEADER
from mcui.config import Settings
from mcui.lxd.client import LXDClient
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import EP1, EP2, fast_store, fixture_json, fixture_text, lxd_err, lxd_ok


def client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def lxd_pair():
    ctx = ssl.create_default_context()
    return LXDClient([EP1, EP2], verify=ctx), LXDClient([EP1, EP2], verify=ctx)


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


async def login(c: httpx.AsyncClient, user: str, pw: str) -> str:
    r = await c.post("/api/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["csrf"]


async def test_login_me_logout(app, services):
    async with client(app) as c:
        r = await c.post("/api/auth/login", json={"username": "admin", "password": "adminpw"})
        assert r.json()["user"] == {"username": "admin", "role": "admin"}
        cookie = r.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Secure" not in cookie
        csrf = r.json()["csrf"]
        me = await c.get("/api/auth/me")
        assert me.json() == {"user": {"username": "admin", "role": "admin"}, "csrf": csrf}
        assert (await c.post("/api/auth/logout")).status_code == 403  # no CSRF header
        r = await c.post("/api/auth/logout", headers={CSRF_HEADER: csrf})
        assert r.status_code == 204
        assert (await c.get("/api/auth/me")).status_code == 401
    actions = [(e["username"], e["action"]) for e in services.store.audit_entries()]
    assert actions == [("admin", "auth.logout"), ("admin", "auth.login")]


async def test_secure_cookie_flag(tmp_path):
    store = fast_store()
    store.add_user("a", "b")
    svc = build_services(Settings(server={"data_dir": str(tmp_path)}), store=store)
    async with client(create_app(services=svc, static_dir=None)) as c:
        r = await c.post("/api/auth/login", json={"username": "a", "password": "b"})
    assert "Secure" in r.headers["set-cookie"]


async def test_bad_login_is_audited(app, services):
    async with client(app) as c:
        r = await c.post("/api/auth/login", json={"username": "admin", "password": "nope"})
        assert r.status_code == 401
        assert (await c.post("/api/auth/login", json={"username": ""})).status_code == 422
    (e,) = services.store.audit_entries()
    assert e["action"] == "auth.login" and e["ok"] is False and e["username"] == "admin"


async def test_unknown_cookie_is_401(app):
    async with client(app) as c:
        c.cookies.set(COOKIE, "forged")
        assert (await c.get("/api/auth/me")).status_code == 401


async def test_viewer_cannot_admin(app):
    async with client(app) as c:
        csrf = await login(c, "viewer", "viewerpw")
        assert (await c.get("/api/users")).status_code == 403
        r = await c.post(
            "/api/users", json={"username": "x", "password": "y"}, headers={CSRF_HEADER: csrf}
        )
        assert r.status_code == 403
        assert (await c.get("/api/audit")).status_code == 403


async def test_user_management(app, services):
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        r = await c.post("/api/users", json={"username": "carol", "password": "pw"}, headers=h)
        assert r.status_code == 201 and r.json() == {"username": "carol", "role": "viewer"}
        dup = await c.post("/api/users", json={"username": "carol", "password": "pw"}, headers=h)
        assert dup.status_code == 409
        r = await c.patch("/api/users/carol", json={"role": "admin", "password": "new"}, headers=h)
        assert r.json() == {"username": "carol", "role": "admin"}
        assert services.store.authenticate("carol", "new") is not None
        r = await c.patch("/api/users/carol", json={"role": "admin"}, headers=h)
        assert r.status_code == 200  # no-op change
        assert (await c.patch("/api/users/nobody", json={}, headers=h)).status_code == 404
        users = (await c.get("/api/users")).json()
        assert {u["username"] for u in users} == {"admin", "viewer", "carol"}
        assert (await c.delete("/api/users/carol", headers=h)).status_code == 204
        audit = (await c.get("/api/audit", params={"limit": 2})).json()
    assert [e["action"] for e in audit] == ["user.delete", "user.update"]
    assert audit[1]["detail"] is None and audit[1]["target"] == "carol"  # the no-op update


async def test_last_admin_protected(app):
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        assert (await c.delete("/api/users/admin", headers=h)).status_code == 409
        r = await c.patch("/api/users/admin", json={"role": "viewer"}, headers=h)
        assert r.status_code == 409
        assert (await c.delete("/api/users/nobody", headers=h)).status_code == 404


async def test_lxd_routes_need_login(app):
    async with client(app) as c:
        for path in ("/api/server", "/api/cluster/members", "/api/metrics/live"):
            assert (await c.get(path)).status_code == 401


@respx.mock
async def test_server_and_members(app):
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(fixture_json("server_clustered.json")))
    respx.get(f"{EP1}/1.0/cluster/members", params={"recursion": "1"}).mock(
        return_value=lxd_ok(fixture_json("cluster_members.json"))
    )
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        server = (await c.get("/api/server")).json()
        assert server["version"] == "5.21.8" and server["endpoint"] == EP1
        assert server["features"]["metrics"] is True
        ms = (await c.get("/api/cluster/members")).json()
        assert [m["name"] for m in ms] == ["m1", "m2"]


@respx.mock
async def test_live_metrics(app):
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(fixture_json("server_clustered.json")))
    respx.get(f"{EP1}/1.0/cluster/members", params={"recursion": "1"}).mock(
        return_value=lxd_ok(fixture_json("cluster_members.json"))
    )
    respx.get("https://192.0.2.1:8443/1.0/metrics").mock(
        return_value=httpx.Response(200, text=fixture_text("metrics_m1_t0.txt"))
    )
    respx.get("https://192.0.2.2:8443/1.0/metrics").mock(return_value=httpx.Response(403))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        body = (await c.get("/api/metrics/live")).json()
    assert {i["name"] for i in body["instances"]} == {"c1", "v1"}
    assert [m["scrape"]["ok"] for m in body["members"]] == [True, False]


@respx.mock
async def test_lxd_errors_mapped(app):
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_err("Forbidden", 403))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        r = await c.get("/api/server")
        assert r.status_code == 502 and r.json() == {"detail": "LXD: Forbidden"}
        respx.get(f"{EP1}/1.0").mock(side_effect=httpx.ConnectError("down"))
        respx.get(f"{EP2}/1.0").mock(side_effect=httpx.ConnectError("down"))
        r = await c.get("/api/server")
        assert r.status_code == 503 and "no LXD endpoint reachable" in r.json()["detail"]


async def test_not_configured(tmp_path):
    store = fast_store()
    store.add_user("v", "pw")
    svc = build_services(
        Settings(server={"data_dir": str(tmp_path), "secure_cookies": False}), store=store
    )
    async with client(create_app(services=svc, static_dir=None)) as c:
        assert (await c.get("/api/health")).json()["lxd_configured"] is False
        await login(c, "v", "pw")
        for path in ("/api/server", "/api/metrics/live"):
            r = await c.get(path)
            assert r.status_code == 503 and r.json()["detail"] == "LXD is not configured"


@respx.mock
async def test_member_resources_and_instances(app):
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(fixture_json("server_clustered.json")))
    respx.get(f"{EP1}/1.0/cluster/members", params={"recursion": "1"}).mock(
        return_value=lxd_ok(fixture_json("cluster_members.json"))
    )
    respx.get(f"{EP1}/1.0/resources", params={"target": "m2"}).mock(
        return_value=lxd_ok({"cpu": {"total": 8}})
    )
    insts = [
        {"name": "b", "project": "p", "type": "container", "status": "Running", "location": "m1"},
        {"name": "a", "project": "p", "type": "virtual-machine", "status": "Stopped"},
    ]
    respx.get(f"{EP1}/1.0/instances").mock(return_value=lxd_ok(insts))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get("/api/cluster/members/m2/resources")).json() == {"cpu": {"total": 8}}
        assert (await c.get("/api/cluster/members/nope/resources")).status_code == 404
        listed = (await c.get("/api/instances")).json()
        assert [i["name"] for i in listed] == ["a", "b"]
        assert (await c.get("/api/instances/p/b")).json()["location"] == "m1"
        assert (await c.get("/api/instances/p/zzz")).status_code == 404


async def test_instances_not_configured(tmp_path):
    store = fast_store()
    store.add_user("v", "pw")
    svc = build_services(
        Settings(server={"data_dir": str(tmp_path), "secure_cookies": False}), store=store
    )
    async with client(create_app(services=svc, static_dir=None)) as c:
        await login(c, "v", "pw")
        assert (await c.get("/api/instances")).status_code == 503
