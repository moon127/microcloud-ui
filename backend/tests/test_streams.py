import json

import httpx
import pytest
import respx
from starlette.websockets import WebSocketDisconnect

from mcui.config import Settings
from mcui.lxd.client import LXDError
from mcui.main import create_app
from mcui.services import build_services
from mcui.streams import STREAM_TTL, Stream, StreamRegistry
from tests.conftest import EP1, fast_store, fixture_json, lxd_err, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair
from tests.test_api_ops import ORIGIN, FakeUpstream, ws_client

FDS = {"0": "data-secret", "control": "ctl-secret"}


def op_resp(location="m2", fds=FDS):
    return httpx.Response(
        202,
        json={
            "type": "async",
            "metadata": {
                "id": "op1",
                "status": "Running",
                "location": location,
                "metadata": {"fds": fds},
            },
        },
    )


@pytest.fixture
def services(tmp_path):
    store = fast_store()
    store.add_user("admin", "adminpw", "admin")
    store.add_user("other", "otherpw", "admin")
    store.add_user("viewer", "viewerpw", "viewer")
    settings = Settings(server={"data_dir": str(tmp_path), "secure_cookies": False})
    return build_services(settings, store=store, clients=lxd_pair())


@pytest.fixture
def app(services):
    return create_app(services=services, static_dir=None)


def mock_members():
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(fixture_json("server_clustered.json")))
    respx.get(f"{EP1}/1.0/cluster/members", params={"recursion": "1"}).mock(
        return_value=lxd_ok(fixture_json("cluster_members.json"))
    )


def test_registry_expiry(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("mcui.streams.time.monotonic", lambda: now[0])
    reg = StreamRegistry()
    old = Stream("a", "exec", "u", "p", "i", "op", None, {"data": "x"}, created=1000.0)
    reg.add(old)
    assert reg.get("a") is old and reg.get("nope") is None
    now[0] += STREAM_TTL
    assert reg.get("a") is None
    reg.add(old)  # re-added with its old creation time: pruned on the next add
    reg.add(Stream("b", "exec", "u", "p", "i", "op", None, {"data": "x"}, created=now[0]))
    assert reg.get("b") is not None and "a" not in reg._streams


@respx.mock
async def test_start_exec_and_console(app, services):
    mock_members()
    ex = respx.post(f"{EP1}/1.0/instances/c1/exec").mock(return_value=op_resp())
    con = respx.post(f"{EP1}/1.0/instances/c1/console").mock(return_value=op_resp(location="none"))
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        r = (
            await c.post("/api/instances/p/c1/exec", json={"cols": 120, "rows": 40}, headers=h)
        ).json()
        assert r["kind"] == "exec" and r["operation"] == "op1"
        body = json.loads(ex.calls.last.request.content)
        assert (
            body["interactive"]
            and body["wait-for-websocket"]
            and (body["width"], body["height"]) == (120, 40)
        )
        assert body["command"][0] == "sh" and body["environment"]["TERM"] == "xterm-256color"
        assert dict(ex.calls.last.request.url.params) == {"project": "p"}
        stream = services.streams.get(r["id"])
        assert stream.base == "https://192.0.2.2:8443"  # the member running the operation
        assert stream.secrets == {"data": "data-secret", "control": "ctl-secret"}
        assert stream.owner == "admin"

        await c.post("/api/instances/p/c1/exec", json={"command": ["bash"]}, headers=h)
        assert json.loads(ex.calls.last.request.content)["command"] == ["bash"]

        r = (await c.post("/api/instances/p/c1/console", json={"type": "vga"}, headers=h)).json()
        assert r["kind"] == "vga" and json.loads(con.calls.last.request.content) == {
            "type": "vga",
            "width": 80,
            "height": 24,
        }
        assert services.streams.get(r["id"]).base is None
        r = (await c.post("/api/instances/p/c1/console", json={}, headers=h)).json()
        assert r["kind"] == "console"
    actions = [e["action"] for e in services.store.audit_entries()]
    assert actions[:4] == ["instance.console", "instance.console", "instance.exec", "instance.exec"]


@respx.mock
async def test_start_errors_and_permissions(app, services):
    mock_members()
    respx.post(f"{EP1}/1.0/instances/c1/exec").mock(return_value=op_resp(location="m9", fds={}))
    respx.post(f"{EP1}/1.0/instances/c2/exec").mock(
        return_value=lxd_err("Instance is not running", 400)
    )
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "viewer", "viewerpw")}
        assert (await c.post("/api/instances/p/c1/exec", json={}, headers=h)).status_code == 403
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        r = await c.post("/api/instances/p/c1/exec", json={}, headers=h)
        assert r.status_code == 502 and "did not return a websocket" in r.json()["detail"]
        r = await c.post("/api/instances/p/c2/exec", json={}, headers=h)
        assert r.status_code == 502 and "not running" in r.json()["detail"]
        assert (
            await c.post("/api/instances/p/c1/exec", json={"cols": 5}, headers=h)
        ).status_code == 422


@respx.mock
async def test_member_url_unknown_location(services):
    from mcui.api.streams import member_url

    mock_members()
    assert await member_url(services.lxd, "m1") == "https://192.0.2.1:8443"
    assert await member_url(services.lxd, "gone") is None
    assert await member_url(services.lxd, None) is None


@respx.mock
async def test_console_log(app, services):
    get = respx.get(f"{EP1}/1.0/instances/c1/console").mock(
        return_value=httpx.Response(200, text="boot\n")
    )
    respx.get(f"{EP1}/1.0/instances/c2/console").mock(
        return_value=lxd_err("Instance not found", 404)
    )
    respx.get(f"{EP1}/1.0/instances/c3/console").mock(return_value=httpx.Response(500, text="oops"))
    respx.delete(f"{EP1}/1.0/instances/c1/console").mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        r = await c.get("/api/instances/p/c1/console-log")
        assert r.text == "boot\n" and dict(get.calls.last.request.url.params) == {"project": "p"}
        r = await c.get("/api/instances/p/c2/console-log")
        assert r.status_code == 502 and r.text == "LXD: Instance not found"
        assert (await c.get("/api/instances/p/c3/console-log")).text == "LXD: HTTP 500"
        assert (await c.delete("/api/instances/p/c1/console-log", headers=h)).status_code == 200
    assert services.store.audit_entries(limit=1)[0]["action"] == "instance.console-log.clear"


# --- attaching ----------------------------------------------------------------------


class Upstreams:
    """Records every LXD websocket the backend opens, by secret."""

    def __init__(self, data_messages=("hello", b"\x1b[0m")):
        self.opened: list[tuple[str, dict, str | None]] = []
        self.by_secret: dict[str, list[FakeUpstream]] = {}
        self.data_messages = data_messages
        self.fail: str | None = None

    async def __call__(self, path, params=None, *, base=None):
        key = params["secret"]
        if self.fail == key:
            raise LXDError("websocket refused: HTTP 403", 403)
        self.opened.append((path, params, base))
        up = FakeUpstream(list(self.data_messages) if key == "data-secret" else [])  # noqa: S105
        self.by_secret.setdefault(key, []).append(up)
        return up


def add_stream(services, owner="admin", secrets=None):
    s = Stream(
        "s1",
        "exec",
        owner,
        "p",
        "c1",
        "op1",
        "https://192.0.2.2:8443",
        secrets or {"data": "data-secret", "control": "ctl-secret"},
    )
    services.streams.add(s)
    return s


def test_attach_relays_data_and_control(app, services, monkeypatch):
    ups = Upstreams()
    monkeypatch.setattr(services.lxd, "websocket", ups)
    stream = add_stream(services)
    with ws_client(app, "admin").websocket_connect("/api/streams/s1", headers=ORIGIN) as ws:
        assert ws.receive_text() == "hello"
        assert ws.receive_bytes() == b"\x1b[0m"  # terminal output is binary
        ws.send_bytes(b"ls\n")
        ws.send_text('{"command":"window-resize"}')
        assert stream.attached == 1 and stream.control is not None
    assert [(p, prm["secret"], b) for p, prm, b in ups.opened] == [
        ("/1.0/operations/op1/websocket", "ctl-secret", "https://192.0.2.2:8443"),
        ("/1.0/operations/op1/websocket", "data-secret", "https://192.0.2.2:8443"),
    ]
    assert ups.by_secret["data-secret"][0].sent == [b"ls\n"]
    assert ups.by_secret["ctl-secret"][0].sent == ['{"command":"window-resize"}']
    assert stream.attached == 0 and stream.control is None
    assert ups.by_secret["ctl-secret"][0].closed


def test_vga_connections_share_one_control(app, services, monkeypatch):
    ups = Upstreams()
    monkeypatch.setattr(services.lxd, "websocket", ups)
    stream = add_stream(services)
    tc = ws_client(app, "admin")
    with tc.websocket_connect("/api/streams/s1", headers=ORIGIN, subprotocols=["binary"]) as a:
        assert a.accepted_subprotocol == "binary"
        a.receive_text()
        with tc.websocket_connect("/api/streams/s1", headers=ORIGIN, subprotocols=["binary"]) as b:
            b.receive_text()
            assert stream.attached == 2
        assert stream.attached == 1 and stream.control is not None
    assert stream.attached == 0
    assert len(ups.by_secret["ctl-secret"]) == 1 and len(ups.by_secret["data-secret"]) == 2


def test_attach_without_control_secret(app, services, monkeypatch):
    ups = Upstreams()
    monkeypatch.setattr(services.lxd, "websocket", ups)
    add_stream(services, secrets={"data": "data-secret"})
    with ws_client(app, "admin").websocket_connect("/api/streams/s1", headers=ORIGIN) as ws:
        assert ws.receive_text() == "hello"
        ws.send_text("dropped: no control channel")
    assert list(ups.by_secret) == ["data-secret"]


@pytest.mark.parametrize(
    "who,reason", [("other", "no such session"), ("viewer", "admin role required")]
)
def test_attach_refused(app, services, who, reason):
    add_stream(services, owner="admin")
    with (
        ws_client(app, who).websocket_connect("/api/streams/s1", headers=ORIGIN) as ws,
        pytest.raises(WebSocketDisconnect) as e,
    ):
        ws.receive_text()
    assert e.value.code == 4403 and e.value.reason == reason


@pytest.mark.parametrize("fail", ["ctl-secret", "data-secret"])
def test_attach_upstream_failure_releases(app, services, monkeypatch, fail):
    ups = Upstreams()
    ups.fail = fail
    monkeypatch.setattr(services.lxd, "websocket", ups)
    stream = add_stream(services)
    with (
        ws_client(app, "admin").websocket_connect("/api/streams/s1", headers=ORIGIN) as ws,
        pytest.raises(WebSocketDisconnect) as e,
    ):
        ws.receive_text()
    assert e.value.code == 4502 and "refused" in e.value.reason
    assert stream.attached == 0 and stream.control is None


def test_attach_unknown_stream(app):
    with (
        ws_client(app, "admin").websocket_connect("/api/streams/nope", headers=ORIGIN) as ws,
        pytest.raises(WebSocketDisconnect) as e,
    ):
        ws.receive_text()
    assert e.value.code == 4403


async def test_release_tolerates_close_errors():
    class Bad:
        async def close(self):
            raise OSError("gone")

    async def connect(secret):
        return Bad()

    s = Stream("x", "vga", "u", "p", "i", "op", None, {"data": "d", "control": "c"})
    await s.acquire_control(connect)
    await s.release_control()
    assert s.control is None
