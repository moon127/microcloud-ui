import asyncio

import httpx
import pytest
import respx
from fastapi import FastAPI, WebSocket
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from mcui.api.wsproxy import relay
from mcui.config import Settings
from mcui.lxd.client import LXDError
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import EP1, fast_store, fixture_json, lxd_err, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair

OP = {"id": "op1", "status": "Running", "created_at": "2026-01-01"}


def async_resp(op=OP):
    return httpx.Response(202, json={"type": "async", "metadata": op})


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


def actions(services):
    return [
        (e["action"], e["target"], e["ok"], e["operation_id"])
        for e in services.store.audit_entries()
    ]


@respx.mock
async def test_operations(app, services):
    respx.get(f"{EP1}/1.0/operations").mock(return_value=lxd_ok({"running": [OP]}))
    respx.get(f"{EP1}/1.0/operations/op1").mock(return_value=lxd_ok(OP))
    w = respx.get(f"{EP1}/1.0/operations/op1/wait").mock(
        return_value=lxd_ok({**OP, "status": "Success"})
    )
    respx.delete(f"{EP1}/1.0/operations/op1").mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "viewer", "viewerpw")}
        assert (await c.get("/api/operations")).json() == [OP]
        assert (await c.get("/api/operations/op1")).json()["status"] == "Running"
        assert (await c.get("/api/operations/op1", params={"wait": 10})).json()[
            "status"
        ] == "Success"
        assert w.calls.last.request.url.params["timeout"] == "10"
        assert (await c.delete("/api/operations/op1", headers=h)).status_code == 403  # viewer
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        assert (await c.delete("/api/operations/op1")).status_code == 403  # no CSRF
        assert (await c.delete("/api/operations/op1", headers=h)).status_code == 204
    assert ("operation.cancel", "op1", True, "op1") in actions(services)


@respx.mock
async def test_warnings(app, services):
    respx.get(f"{EP1}/1.0/warnings").mock(
        return_value=lxd_ok(
            [{"uuid": "a", "last_seen_at": "1"}, {"uuid": "b", "last_seen_at": "2"}]
        )
    )
    respx.put(f"{EP1}/1.0/warnings/a").mock(return_value=lxd_ok(None))
    respx.delete(f"{EP1}/1.0/warnings/a").mock(return_value=lxd_ok(None))
    respx.delete(f"{EP1}/1.0/warnings/zz").mock(return_value=lxd_err("Warning not found", 404))
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        assert [w["uuid"] for w in (await c.get("/api/warnings")).json()] == ["b", "a"]
        r = await c.put("/api/warnings/a", json={"status": "acknowledged"}, headers=h)
        assert r.status_code == 200
        assert (
            await c.put("/api/warnings/a", json={"status": "bogus"}, headers=h)
        ).status_code == 422
        assert (await c.delete("/api/warnings/a", headers=h)).status_code == 204
        r = await c.delete("/api/warnings/zz", headers=h)
        assert r.status_code == 502 and "Warning not found" in r.json()["detail"]
    assert actions(services)[:3] == [
        ("warning.delete", "zz", False, None),
        ("warning.delete", "a", True, None),
        ("warning.update", "a", True, None),
    ]
    failed = services.store.audit_entries()[0]
    assert failed["detail"] == {"error": "Warning not found"}


@respx.mock
async def test_evacuate_restore(app, services):
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(fixture_json("server_clustered.json")))
    respx.get(f"{EP1}/1.0/cluster/members", params={"recursion": "1"}).mock(
        return_value=lxd_ok(fixture_json("cluster_members.json"))
    )
    route = respx.post(f"{EP1}/1.0/cluster/members/m2/state").mock(return_value=async_resp())
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        r = await c.post("/api/cluster/members/m2/evacuate", json={"mode": "stop"}, headers=h)
        assert r.json()["operation"]["id"] == "op1"
        assert route.calls.last.request.content == b'{"action":"evacuate","mode":"stop"}'
        await c.post("/api/cluster/members/m2/restore", json={}, headers=h)
        assert route.calls.last.request.content == b'{"action":"restore"}'
        r = await c.post("/api/cluster/members/nope/restore", json={}, headers=h)
        assert r.status_code == 404
    assert actions(services)[:2] == [
        ("member.restore", "m2", True, "op1"),
        ("member.evacuate", "m2", True, "op1"),
    ]


@respx.mock
async def test_projects(app):
    respx.get(f"{EP1}/1.0/projects").mock(
        return_value=lxd_ok(
            [
                {"name": "default", "config": {"features.images": "true"}},
                {"name": "mcui-test", "used_by": ["/x"]},
            ]
        )
    )
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get("/api/projects")).json() == [
            {
                "name": "default",
                "description": "",
                "config": {"features.images": "true"},
                "used_by": [],
            },
            {"name": "mcui-test", "description": "", "config": {}, "used_by": ["/x"]},
        ]


# --- events websocket -------------------------------------------------------


class FakeUpstream:
    def __init__(self, messages):
        self.messages = messages
        self.sent = []
        self.closed = False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for m in self.messages:
            yield m
        await asyncio.sleep(3600)  # stay open like a real event stream

    async def send(self, m):
        self.sent.append(m)

    async def close(self):
        self.closed = True


def ws_client(app, user="viewer"):
    tc = TestClient(app)
    r = tc.post("/api/auth/login", json={"username": user, "password": f"{user}pw"})
    assert r.status_code == 200
    return tc


ORIGIN = {"origin": "http://testserver"}


def test_events_relay(app, services, monkeypatch):
    up = FakeUpstream(['{"type":"operation"}', '{"type":"operation"}', b"\x01", '{"type":"x"}'])
    calls = []

    async def fake_ws(path, params=None, **kw):
        calls.append((path, params))
        return up

    monkeypatch.setattr(services.lxd, "websocket", fake_ws)
    with ws_client(app).websocket_connect("/api/events", headers=ORIGIN) as ws:
        assert ws.receive_text() == '{"type":"operation"}'
        assert ws.receive_bytes() == b"\x01"  # the repeated event was dropped
        assert ws.receive_text() == '{"type":"x"}'
        ws.send_text("ignored")  # events are one-way
    assert calls == [("/1.0/events", {"type": "operation,lifecycle", "all-projects": "true"})]
    assert up.sent == []


@pytest.mark.parametrize(
    "headers,login_first,code",
    [
        ({"origin": "http://evil.example"}, True, 4403),
        ({}, True, 4403),
        (ORIGIN, False, 4401),
    ],
)
def test_events_refused(app, headers, login_first, code):
    tc = ws_client(app) if login_first else TestClient(app)
    with (
        tc.websocket_connect("/api/events", headers=headers) as ws,
        pytest.raises(WebSocketDisconnect) as e,
    ):
        ws.receive_text()
    assert e.value.code == code


def test_events_upstream_error(app, services, monkeypatch):
    async def boom(*a, **kw):
        raise LXDError("no LXD endpoint reachable")

    monkeypatch.setattr(services.lxd, "websocket", boom)
    with (
        ws_client(app).websocket_connect("/api/events", headers=ORIGIN) as ws,
        pytest.raises(WebSocketDisconnect) as e,
    ):
        ws.receive_text()
    assert e.value.code == 4502 and "no LXD endpoint" in e.value.reason


def test_events_without_lxd(tmp_path):
    store = fast_store()
    store.add_user("viewer", "viewerpw")
    svc = build_services(
        Settings(server={"data_dir": str(tmp_path), "secure_cookies": False}), store=store
    )
    app = create_app(services=svc, static_dir=None)
    with (
        ws_client(app).websocket_connect("/api/events", headers=ORIGIN) as ws,
        pytest.raises(WebSocketDisconnect) as e,
    ):
        ws.receive_text()
    assert e.value.code == 4502


def test_relay_both_ways():
    """The relay used by consoles: client frames go upstream, text and bytes kept."""
    up = FakeUpstream(["hello"])
    app = FastAPI()

    @app.websocket("/x")
    async def x(ws: WebSocket):
        await ws.accept()
        await relay(ws, up)

    with TestClient(app).websocket_connect("/x") as ws:
        assert ws.receive_text() == "hello"
        ws.send_bytes(b"\x03")
        ws.send_text("ls\n")
        ws.send({"type": "websocket.receive"})  # neither text nor bytes: ignored
    assert up.sent == [b"\x03", "ls\n"] and up.closed


def test_admin_only_websocket(app):
    """Consoles (3d) will require the admin role on the websocket too."""
    from mcui.api.wsproxy import authenticate

    @app.websocket("/api/test-admin")
    async def admin_ws(ws: WebSocket):
        if await authenticate(ws, admin=True) is not None:
            await ws.send_text("ok")
            await ws.close()

    with (
        ws_client(app, "viewer").websocket_connect("/api/test-admin", headers=ORIGIN) as ws,
        pytest.raises(WebSocketDisconnect) as e,
    ):
        ws.receive_text()
    assert e.value.code == 4403 and e.value.reason == "admin role required"
    with ws_client(app, "admin").websocket_connect("/api/test-admin", headers=ORIGIN) as ws:
        assert ws.receive_text() == "ok"


def test_dedupe_window():
    from mcui.api.wsproxy import Dedupe

    d = Dedupe(size=2)
    assert [d(m) for m in ["a", "a", "b", "c", "a", "c"]] == [True, False, True, True, True, False]


def test_relay_closes_browser_when_lxd_ends():
    class Ending(FakeUpstream):
        async def _gen(self):
            for m in self.messages:
                yield m

    up = Ending(["bye"])
    app = FastAPI()

    @app.websocket("/x")
    async def x(ws: WebSocket):
        await ws.accept()
        await relay(ws, up)

    with TestClient(app).websocket_connect("/x") as ws:
        assert ws.receive_text() == "bye"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
    assert up.closed


@pytest.mark.parametrize(
    "public_url,headers,code",
    [
        # a proxy that drops the public port from Host (NGINX Proxy Manager's $host)
        (None, {"origin": "https://mcui.example.org:8443", "host": "mcui.example.org"}, 4403),
        (
            "https://mcui.example.org:8443",
            {"origin": "https://mcui.example.org:8443", "host": "mcui.example.org"},
            4502,
        ),
        # a proxy that rewrites Host to the upstream address
        (
            "https://MCUI.example.org/",
            {"origin": "https://mcui.example.org", "host": "127.0.0.1:8843"},
            4502,
        ),
        # public_url doesn't open the door to other sites
        ("https://mcui.example.org", {"origin": "https://evil.example", "host": "x"}, 4403),
        ("https://mcui.example.org", {"origin": "http://mcui.example.org", "host": "x"}, 4403),
    ],
)
def test_events_origin_behind_proxy(tmp_path, public_url, headers, code):
    """Past the Origin check the socket closes 4502 (no LXD here); refused is 4403 (D65)."""
    store = fast_store()
    store.add_user("viewer", "viewerpw")
    server = {"data_dir": str(tmp_path), "secure_cookies": False, "public_url": public_url}
    svc = build_services(Settings(server=server), store=store)
    app = create_app(services=svc, static_dir=None)
    with (
        ws_client(app).websocket_connect("/api/events", headers=headers) as ws,
        pytest.raises(WebSocketDisconnect) as e,
    ):
        ws.receive_text()
    assert e.value.code == code
