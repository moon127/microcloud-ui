import json

import httpx
import pytest
import respx

from mcui.config import Settings
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import EP1, fast_store, lxd_err, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair

OP = {"id": "op1", "status": "Running", "created_at": "2026-01-01"}
INST = f"{EP1}/1.0/instances"


def async_resp():
    return httpx.Response(202, json={"type": "async", "metadata": OP})


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


async def admin(c):
    return {CSRF_HEADER: await login(c, "admin", "adminpw")}


def body(route):
    return json.loads(route.calls.last.request.content)


def params(route):
    return dict(route.calls.last.request.url.params)


def last_audit(services):
    e = services.store.audit_entries(limit=1)[0]
    return e["action"], e["target"], e["ok"], e["operation_id"], e["detail"]


@respx.mock
async def test_list_detail_and_logs(app):
    respx.get(INST, params={"recursion": "2"}).mock(
        return_value=lxd_ok(
            [{"name": "c1", "project": "p", "profiles": ["default"], "status": "Running"}]
        )
    )
    detail = respx.get(f"{INST}/c1").mock(return_value=lxd_ok({"name": "c1", "config": {}}))
    respx.get(f"{INST}/c1/logs").mock(
        return_value=lxd_ok(["/1.0/instances/c1/logs/lxc.log", "/1.0/instances/c1/logs/a.log"])
    )
    respx.get(f"{INST}/c1/logs/lxc.log").mock(return_value=httpx.Response(200, text="hello\n"))
    respx.get(f"{INST}/c1/logs/gone.log").mock(return_value=httpx.Response(404, text=""))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        listed = (await c.get("/api/instances")).json()
        assert listed[0]["profiles"] == ["default"]
        assert (await c.get("/api/instances/p/c1")).json() == {"name": "c1", "config": {}}
        assert params(detail) == {"project": "p", "recursion": "1"}
        assert (await c.get("/api/instances/p/c1/logs")).json() == ["a.log", "lxc.log"]
        r = await c.get("/api/instances/p/c1/logs/lxc.log")
        assert r.text == "hello\n" and r.headers["content-type"].startswith("text/plain")
        assert (await c.get("/api/instances/p/c1/logs/gone.log")).status_code == 404
        assert (await c.get("/api/instances/p/c1/logs/..hidden")).status_code == 422
        # an encoded ".." reaches the server and must never be passed to LXD
        assert (await c.get("/api/instances/p/%2E%2E")).status_code == 422
        assert (await c.delete("/api/instances/%2E%2E/c1")).status_code in (403, 422)


@respx.mock
async def test_state_changes_need_admin_and_are_audited(app, services):
    route = respx.put(f"{INST}/c1/state").mock(return_value=async_resp())
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "viewer", "viewerpw")}
        assert (
            await c.put("/api/instances/p/c1/state", json={"action": "start"}, headers=h)
        ).status_code == 403
        h = await admin(c)
        r = await c.put(
            "/api/instances/p/c1/state", json={"action": "stop", "force": True}, headers=h
        )
        assert r.json()["operation"]["id"] == "op1"
        assert body(route) == {"action": "stop", "force": True, "stateful": False, "timeout": 30}
        assert params(route) == {"project": "p"}
        assert last_audit(services) == ("instance.stop", "p/c1", True, "op1", {"force": True})
        r = await c.put("/api/instances/p/c1/state", json={"action": "explode"}, headers=h)
        assert r.status_code == 422


@respx.mock
async def test_delete_plain_and_forced(app, services):
    state = respx.get(f"{INST}/c1/state").mock(return_value=lxd_ok({"status": "Running"}))
    stop = respx.put(f"{INST}/c1/state").mock(return_value=async_resp())
    respx.get(f"{EP1}/1.0/operations/op1/wait").mock(
        return_value=lxd_ok({**OP, "status": "Success"})
    )
    delete = respx.delete(f"{INST}/c1").mock(return_value=async_resp())
    async with client(app) as c:
        h = await admin(c)
        await c.delete("/api/instances/p/c1", headers=h)
        assert not state.called and delete.called
        assert last_audit(services)[0:3] == ("instance.delete", "p/c1", True)
        await c.delete("/api/instances/p/c1", params={"force": "true"}, headers=h)
        assert body(stop) == {"action": "stop", "force": True, "timeout": -1}
        assert last_audit(services)[4] == {"force": True}
        # a failed stop is audited as a failed delete
        respx.get(f"{EP1}/1.0/operations/op1/wait").mock(
            return_value=lxd_ok({**OP, "status": "Failure", "err": "stop timed out"})
        )
        r = await c.delete("/api/instances/p/c1", params={"force": "true"}, headers=h)
        assert r.status_code == 502
        assert last_audit(services)[0:3] == ("instance.delete", "p/c1", False)
        assert last_audit(services)[4] == {"force": True, "error": "stop timed out"}
        state.mock(return_value=lxd_ok({"status": "Stopped"}))
        stop.reset()
        await c.delete("/api/instances/p/c1", params={"force": "true"}, headers=h)
        assert not stop.called


@respx.mock
async def test_update_and_patch_config(app, services):
    put = respx.put(f"{INST}/c1").mock(return_value=async_resp())
    patch = respx.patch(f"{INST}/c1").mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = await admin(c)
        upd = {
            "config": {"limits.cpu": "2"},
            "devices": {"root": {"type": "disk", "path": "/", "pool": "remote"}},
            "profiles": ["default"],
        }
        await c.put("/api/instances/p/c1", json=upd, headers=h)
        assert body(put) == {
            "description": "",
            "config": {"limits.cpu": "2"},
            "devices": {"root": {"type": "disk", "path": "/", "pool": "remote"}},
            "profiles": ["default"],
            "ephemeral": False,
            "stateful": False,
        }
        r = await c.patch(
            "/api/instances/p/c1/config",
            json={"config": {"snapshots.schedule": "@daily"}},
            headers=h,
        )
        assert r.json() == {"operation": None, "metadata": None}
        assert body(patch) == {"config": {"snapshots.schedule": "@daily"}}
        assert last_audit(services) == (
            "instance.update",
            "p/c1",
            True,
            None,
            {"keys": ["snapshots.schedule"]},
        )


@respx.mock
async def test_rename_move_copy(app, services):
    post = respx.post(f"{INST}/c1").mock(return_value=async_resp())
    create = respx.post(INST).mock(return_value=async_resp())
    async with client(app) as c:
        h = await admin(c)
        await c.post("/api/instances/p/c1/rename", json={"name": "c2"}, headers=h)
        assert body(post) == {"name": "c2"}
        assert (
            await c.post("/api/instances/p/c1/rename", json={"name": "bad/name"}, headers=h)
        ).status_code == 422
        await c.post("/api/instances/p/c1/move", json={"target": "m2", "live": True}, headers=h)
        assert body(post) == {"name": "c1", "migration": True, "live": True}
        assert params(post) == {"project": "p", "target": "m2"}
        await c.post("/api/instances/p/c1/move", json={"pool": "local"}, headers=h)
        assert body(post) == {"name": "c1", "migration": True, "live": False, "pool": "local"}
        assert params(post) == {"project": "p"}
        r = await c.post("/api/instances/p/c1/move", json={}, headers=h)
        assert r.status_code == 400
        await c.post(
            "/api/instances/p/c1/copy",
            json={"name": "c3", "snapshot": "s1", "target": "m3", "start": True},
            headers=h,
        )
        assert body(create) == {
            "name": "c3",
            "source": {"type": "copy", "source": "c1/s1", "instance_only": True},
            "start": True,
        }
        assert params(create) == {"project": "p", "target": "m3"}
        await c.post(
            "/api/instances/p/c1/copy", json={"name": "c4", "instance_only": False}, headers=h
        )
        assert body(create)["source"] == {"type": "copy", "source": "c1", "instance_only": False}
        assert last_audit(services) == (
            "instance.copy",
            "p/c1",
            True,
            "op1",
            {"to": "c4", "from": "c1"},
        )


@respx.mock
async def test_snapshots(app, services):
    create = respx.post(f"{INST}/c1/snapshots").mock(return_value=async_resp())
    rename = respx.post(f"{INST}/c1/snapshots/s1").mock(return_value=async_resp())
    delete = respx.delete(f"{INST}/c1/snapshots/s1").mock(return_value=async_resp())
    restore = respx.put(f"{INST}/c1").mock(return_value=async_resp())
    async with client(app) as c:
        h = await admin(c)
        await c.post(
            "/api/instances/p/c1/snapshots",
            json={"name": "s1", "expires_at": "2026-12-01T00:00:00Z"},
            headers=h,
        )
        assert body(create) == {
            "name": "s1",
            "stateful": False,
            "expires_at": "2026-12-01T00:00:00Z",
        }
        await c.post("/api/instances/p/c1/snapshots/s1/rename", json={"name": "s2"}, headers=h)
        assert body(rename) == {"name": "s2"}
        await c.post("/api/instances/p/c1/snapshots/s1/restore", json={}, headers=h)
        assert body(restore) == {"restore": "s1", "stateful": False}
        # LXD 6.6+: with the exclusively attached volumes (recorded live on the 6.10 VM)
        await c.post(
            "/api/instances/p/c1/snapshots",
            json={"name": "s3", "disk_volumes_mode": "all-exclusive"},
            headers=h,
        )
        assert body(create) == {
            "name": "s3",
            "stateful": False,
            "disk_volumes_mode": "all-exclusive",
        }
        assert last_audit(services)[4] == {"snapshot": "s3", "volumes": True}
        await c.post(
            "/api/instances/p/c1/snapshots/s3/restore",
            json={"disk_volumes_mode": "all-exclusive"},
            headers=h,
        )
        assert body(restore) == {
            "restore": "s3",
            "stateful": False,
            "disk_volumes_mode": "all-exclusive",
        }
        r = await c.post(
            "/api/instances/p/c1/snapshots",
            json={"name": "x", "disk_volumes_mode": "all"},
            headers=h,
        )
        assert r.status_code == 422
        await c.delete("/api/instances/p/c1/snapshots/s1", headers=h)
        assert delete.called
        assert last_audit(services) == ("snapshot.delete", "p/c1", True, "op1", {"snapshot": "s1"})


@respx.mock
async def test_publish(app, services):
    route = respx.post(f"{EP1}/1.0/images").mock(return_value=async_resp())
    async with client(app) as c:
        h = await admin(c)
        await c.post(
            "/api/instances/p/c1/publish",
            json={"alias": "golden", "properties": {"os": "alpine"}},
            headers=h,
        )
        assert body(route) == {
            "source": {"type": "instance", "name": "c1"},
            "public": False,
            "properties": {"os": "alpine"},
            "aliases": [{"name": "golden"}],
        }
        assert params(route) == {"project": "p"}
        await c.post("/api/instances/p/c1/publish", json={"snapshot": "s1"}, headers=h)
        assert body(route) == {
            "source": {"type": "snapshot", "name": "c1/s1"},
            "public": False,
            "properties": {},
        }


@respx.mock
async def test_lxd_refusal_is_audited_and_cache_kept(app, services):
    respx.put(f"{INST}/c1/state").mock(return_value=lxd_err("Instance is already running", 400))
    respx.get(INST, params={"recursion": "2"}).mock(return_value=lxd_ok([{"name": "c1"}]))
    async with client(app) as c:
        h = await admin(c)
        await c.get("/api/instances")
        r = await c.put("/api/instances/p/c1/state", json={"action": "start"}, headers=h)
        assert r.status_code == 502 and "already running" in r.json()["detail"]
    action, target, ok, _, detail = last_audit(services)
    assert (action, ok, detail) == (
        "instance.start",
        False,
        {"error": "Instance is already running"},
    )
    assert services.instances._items is not None  # nothing changed, cache kept


@respx.mock
async def test_success_invalidates_cache(app, services):
    respx.put(f"{INST}/c1/state").mock(return_value=async_resp())
    lst = respx.get(INST, params={"recursion": "2"}).mock(return_value=lxd_ok([]))
    async with client(app) as c:
        h = await admin(c)
        await c.get("/api/instances")
        await c.put("/api/instances/p/c1/state", json={"action": "start"}, headers=h)
        await c.get("/api/instances")
    assert lst.call_count == 2


async def test_not_configured(tmp_path):
    store = fast_store()
    store.add_user("v", "pw")
    svc = build_services(
        Settings(server={"data_dir": str(tmp_path), "secure_cookies": False}), store=store
    )
    async with client(create_app(services=svc, static_dir=None)) as c:
        await login(c, "v", "pw")
        assert (await c.get("/api/instances")).status_code == 503
