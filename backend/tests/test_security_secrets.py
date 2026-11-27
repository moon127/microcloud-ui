"""Exec and console websocket secrets never reach the browser (0.3.4, D88).

Found in the 2026-11-27 review and confirmed live on LXD 6.10.1: an exec operation's
``metadata.fds`` holds its websocket secrets, in ``GET /1.0/operations/{id}`` and in
the operations list, and LXD's operation websocket accepts the secret alone. Every
operation the app hands out, and every event it relays, was passing them on to anyone
who could see the operation, viewers included."""

import json

import respx

from mcui.api.wsproxy import redact_event
from mcui.lxd.operations import Started, redact
from tests.conftest import EP1, lxd_ok
from tests.test_api import client, login
from tests.test_api_ops import ORIGIN, FakeUpstream, app, services, ws_client

__all__ = ["app", "services"]  # fixtures

SECRET = "a" * 64
EXEC = {
    "id": "op-exec",
    "class": "websocket",
    "status": "Running",
    "created_at": "2026-11-27T10:00:00Z",
    "resources": {"instances": ["/1.0/instances/c1?project=web"]},
    "metadata": {"command": ["bash"], "environment": {}, "fds": {"0": SECRET, "control": SECRET}},
    "project": "web",
}
BULK = {**EXEC, "id": "op-bulk", "metadata": None, "children": [EXEC]}


def test_redact():
    out = redact(BULK)
    assert "fds" not in out["children"][0]["metadata"]
    assert out["children"][0]["metadata"]["command"] == ["bash"]
    assert BULK["children"][0]["metadata"]["fds"]  # the original is untouched
    assert redact({"metadata": {"secret": "s", "secrets": {}, "x": 1}}) == {"metadata": {"x": 1}}
    assert redact(None) is None and redact({"id": "x"}) == {"id": "x"}
    st = Started(operation=EXEC)
    assert SECRET not in json.dumps(st.as_dict())
    assert st.operation["metadata"]["fds"]  # kept server-side for the terminal relay


@respx.mock
async def test_operations_list_and_get_have_no_secrets(app):
    respx.get(f"{EP1}/1.0/operations").mock(return_value=lxd_ok({"running": [EXEC]}))
    respx.get(f"{EP1}/1.0/operations/op-exec").mock(return_value=lxd_ok(EXEC))
    respx.get(f"{EP1}/1.0/operations/op-exec/wait").mock(return_value=lxd_ok(EXEC))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        for url in (
            "/api/operations",
            "/api/operations/op-exec",
            "/api/operations/op-exec?wait=5",
        ):
            r = await c.get(url)
            assert r.status_code == 200 and SECRET not in r.text, url
            assert "command" in r.text


def test_redact_event():
    event = json.dumps({"type": "operation", "project": "web", "metadata": EXEC})
    out = redact_event(event)
    assert SECRET not in out and json.loads(out)["metadata"]["metadata"]["command"] == ["bash"]
    assert redact_event(event.encode()) != event.encode()  # bytes frames too
    for msg in ('{"type":"lifecycle"}', b"\x01", '{"fds" broken', '["fds"]', '{"metadata":"fds"}'):
        assert redact_event(msg) == msg


def test_events_relay_strips_secrets(app, services, monkeypatch):
    event = json.dumps({"type": "operation", "project": "web", "metadata": EXEC})
    up = FakeUpstream([event])

    async def fake_ws(path, params=None, **kw):
        return up

    monkeypatch.setattr(services.lxd, "websocket", fake_ws)
    with ws_client(app).websocket_connect("/api/events", headers=ORIGIN) as ws:
        got = ws.receive_text()
    assert SECRET not in got and json.loads(got)["metadata"]["id"] == "op-exec"


MIGRATION = {
    "id": "op-move",
    "class": "websocket",
    "status": "Running",
    "project": "web",
    "metadata": {"control": SECRET, "fs": SECRET, "criu": SECRET},
}


def test_migration_secrets_are_redacted_too():
    """A migration's metadata is the secrets map itself (LXD's migrationSourceWs, D90)."""
    assert redact(MIGRATION)["metadata"] == {}
    exec_kept = redact({**EXEC, "metadata": {**EXEC["metadata"], "environment": {"T": "x"}}})
    assert exec_kept["metadata"] == {"command": ["bash"]}  # no fds, no environment
    event = json.dumps({"type": "operation", "metadata": MIGRATION})
    assert SECRET not in redact_event(event)
    assert redact({"class": "task", "metadata": {"fs": "kept: not a websocket"}})["metadata"]


@respx.mock
async def test_migration_operations_have_no_secrets(app):
    respx.get(f"{EP1}/1.0/operations").mock(return_value=lxd_ok({"running": [MIGRATION]}))
    respx.get(f"{EP1}/1.0/operations/op-move").mock(return_value=lxd_ok(MIGRATION))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        for url in ("/api/operations", "/api/operations/op-move"):
            assert SECRET not in (await c.get(url)).text, url
