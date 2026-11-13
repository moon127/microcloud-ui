import httpx
import pytest
import respx

from mcui.config import Settings
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import EP1, fast_store, lxd_err, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair
from tests.test_manage import admin, audit, body, params

POOLS = f"{EP1}/1.0/storage-pools"
VOL = f"{POOLS}/local/volumes/custom/v1"


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
async def test_list_across_pools(app):
    respx.get(POOLS).mock(return_value=lxd_ok([{"name": "local"}, {"name": "remote"}]))
    loc = respx.get(f"{POOLS}/local/volumes").mock(
        return_value=lxd_ok(
            [
                {"name": "v1", "type": "custom"},
                {"name": "v1/s0", "type": "custom"},
                {"name": "c1", "type": "container"},
            ]
        )
    )
    respx.get(f"{POOLS}/remote/volumes").mock(return_value=lxd_err("Pool unavailable", 503))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        r = await c.get("/api/volumes", params={"project": "p"})
        assert r.json() == [{"name": "v1", "type": "custom", "pool": "local"}]
        assert params(loc) == {"recursion": "1", "project": "p"}
        r = await c.get("/api/volumes", params={"pool": "local", "all": "true"})
        assert [v["name"] for v in r.json()] == ["v1", "c1"]


@respx.mock
async def test_detail_and_snapshots(app):
    get = respx.get(VOL).mock(return_value=lxd_ok({"name": "v1", "config": {"size": "1GiB"}}))
    state = respx.get(f"{VOL}/state").mock(return_value=lxd_ok({"usage": {"used": 5}}))
    snaps = respx.get(f"{VOL}/snapshots").mock(return_value=lxd_ok([{"name": "v1/s1"}]))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        d = (await c.get("/api/volumes/p/local/v1", params={"target": "m1"})).json()
        assert d == {
            "name": "v1",
            "config": {"size": "1GiB"},
            "pool": "local",
            "state": {"usage": {"used": 5}},
        }
        assert params(get) == {"project": "p", "target": "m1"} == params(state)
        state.mock(return_value=lxd_err("not supported", 400))
        assert (await c.get("/api/volumes/p/local/v1")).json()["state"] is None
        assert (await c.get("/api/volumes/p/local/v1/snapshots")).json() == [{"name": "v1/s1"}]
        assert params(snaps) == {"recursion": "1", "project": "p"}


@respx.mock
async def test_create_update_rename_delete(app, services):
    create = respx.post(f"{POOLS}/local/volumes/custom").mock(return_value=lxd_ok(None))
    put = respx.put(VOL).mock(return_value=lxd_ok(None))
    respx.get(VOL).mock(
        return_value=lxd_ok(
            {"config": {"size": "1GiB", "volatile.uuid": "u1", "snapshots.expiry": "1d"}}
        )
    )
    ren = respx.post(VOL).mock(return_value=lxd_ok(None))
    dele = respx.delete(VOL).mock(return_value=lxd_err("Volume is in use", 400))
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "viewer", "viewerpw")}
        new = {"name": "v1", "pool": "local", "project": "p", "config": {"size": "1GiB"}}
        assert (await c.post("/api/volumes", json=new, headers=h)).status_code == 403
        h = await admin(c)
        await c.post(
            "/api/volumes", json={**new, "content_type": "block", "target": "m2"}, headers=h
        )
        assert body(create) == {
            "name": "v1",
            "type": "custom",
            "content_type": "block",
            "description": "",
            "config": {"size": "1GiB"},
        }
        assert params(create) == {"project": "p", "target": "m2"}
        await c.post("/api/volumes", json={"name": "v2", "pool": "local"}, headers=h)
        assert params(create) == {"project": "default"}
        bad = {"name": "v", "pool": "local", "content_type": "iso"}
        assert (await c.post("/api/volumes", json=bad, headers=h)).status_code == 422
        await c.put(
            "/api/volumes/p/local/v1",
            params={"target": "m2"},
            json={"config": {"size": "2GiB"}},
            headers=h,
        )
        # volatile keys survive; others are replaced
        assert body(put) == {"description": "", "config": {"size": "2GiB", "volatile.uuid": "u1"}}
        assert params(put) == {"project": "p", "target": "m2"}
        await c.post("/api/volumes/p/local/v1/rename", json={"name": "v9"}, headers=h)
        assert body(ren) == {"name": "v9"}
        r = await c.delete("/api/volumes/p/local/v1", headers=h)
        assert r.status_code == 502 and "in use" in r.text
    assert dele.call_count == 1
    assert [a[0] for a in audit(services, 5)] == [
        "volume.delete",
        "volume.rename",
        "volume.update",
        "volume.create",
        "volume.create",
    ]
    assert audit(services, 5)[4][3] == {"content_type": "block", "size": "1GiB"}


@respx.mock
async def test_snapshots(app, services):
    create = respx.post(f"{VOL}/snapshots").mock(return_value=lxd_ok(None))
    ren = respx.post(f"{VOL}/snapshots/s1").mock(return_value=lxd_ok(None))
    dele = respx.delete(f"{VOL}/snapshots/s1").mock(return_value=lxd_ok(None))
    respx.get(VOL).mock(return_value=lxd_ok({"description": "d", "config": {"size": "1GiB"}}))
    put = respx.put(VOL).mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = await admin(c)
        await c.post("/api/volumes/p/local/v1/snapshots", json={"name": "s1"}, headers=h)
        assert body(create) == {"name": "s1"}
        await c.post(
            "/api/volumes/p/local/v1/snapshots",
            params={"target": "m1"},
            json={"name": "s2", "expires_at": "2030-01-01T00:00:00Z"},
            headers=h,
        )
        assert body(create)["expires_at"] and params(create)["target"] == "m1"
        await c.post("/api/volumes/p/local/v1/snapshots/s1/rename", json={"name": "s3"}, headers=h)
        assert body(ren) == {"name": "s3"}
        await c.post("/api/volumes/p/local/v1/snapshots/s1/restore", headers=h)
        assert body(put) == {"description": "d", "config": {"size": "1GiB"}, "restore": "s1"}
        await c.delete("/api/volumes/p/local/v1/snapshots/s1", headers=h)
        assert dele.call_count == 1
    assert [a[0] for a in audit(services, 5)] == [
        "volume.snapshot.delete",
        "volume.snapshot.restore",
        "volume.snapshot.rename",
        "volume.snapshot.create",
        "volume.snapshot.create",
    ]


INST = f"{EP1}/1.0/instances/web"


def inst(devices):
    return {
        "name": "web",
        "status": "Running",
        "architecture": "x86_64",
        "config": {"limits.cpu": "1"},
        "devices": devices,
        "ephemeral": False,
        "profiles": ["default"],
        "stateful": False,
        "description": "",
    }


@respx.mock
async def test_attach_detach(app, services):
    patch = respx.patch(INST).mock(return_value=lxd_ok(None))
    dev = {"type": "disk", "pool": "local", "source": "v1", "path": "/data"}
    respx.get(INST).mock(
        return_value=httpx.Response(
            200,
            json={"type": "sync", "metadata": inst({"data": dev, "eth0": {"type": "nic"}})},
            headers={"ETag": "abc"},
        )
    )
    put = respx.put(INST).mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = await admin(c)
        url = "/api/volumes/p/local/v1"
        await c.post(
            f"{url}/attach", json={"instance": "web", "device": "data", "path": "/data"}, headers=h
        )
        assert body(patch) == {"devices": {"data": dev}} and params(patch) == {"project": "p"}
        await c.post(f"{url}/attach", json={"instance": "web", "device": "blk"}, headers=h)
        assert body(patch) == {
            "devices": {"blk": {"type": "disk", "pool": "local", "source": "v1"}}
        }
        r = await c.post(
            f"{url}/attach", json={"instance": "web", "device": "x", "path": "rel"}, headers=h
        )
        assert r.status_code == 422
        # an ISO, booted first (Phase 7d)
        iso = {"instance": "web", "device": "iso", "boot_priority": 10}
        await c.post(f"{url}/attach", json=iso, headers=h)
        assert body(patch)["devices"]["iso"] == {
            "type": "disk",
            "pool": "local",
            "source": "v1",
            "boot.priority": "10",
        }
        bad = {**iso, "boot_priority": 101}
        assert (await c.post(f"{url}/attach", json=bad, headers=h)).status_code == 422
        r = await c.post(f"{url}/detach", json={"instance": "web", "device": "data"}, headers=h)
        assert r.status_code == 200, r.text
        sent = body(put)
        assert sent["devices"] == {"eth0": {"type": "nic"}} and "status" not in sent
        assert sent["config"] == {"limits.cpu": "1"}
        assert put.calls.last.request.headers["If-Match"] == "abc"
        r = await c.post(f"{url}/detach", json={"instance": "web", "device": "eth0"}, headers=h)
        assert r.status_code == 409 and "not this volume" in r.text
    assert [a[0] for a in audit(services, 3)] == ["volume.detach", "volume.attach", "volume.attach"]


@respx.mock
async def test_detach_errors(app, services):
    respx.get(INST).mock(return_value=lxd_err("Instance not found", 404))
    async with client(app) as c:
        h = await admin(c)
        r = await c.post(
            "/api/volumes/p/local/v1/detach", json={"instance": "web", "device": "d"}, headers=h
        )
        assert r.status_code in (404, 502) and "not found" in r.text
    respx.get(INST).mock(
        return_value=lxd_ok(inst({"d": {"type": "disk", "pool": "local", "source": "v1"}}))
    )
    respx.put(INST).mock(return_value=lxd_err("ETag doesn't match", 412))
    async with client(app) as c:
        h = await admin(c)
        r = await c.post(
            "/api/volumes/p/local/v1/detach", json={"instance": "web", "device": "d"}, headers=h
        )
        assert "ETag" in r.text
    respx.put(INST).mock(
        return_value=httpx.Response(202, json={"type": "async", "metadata": {"id": "op9"}})
    )
    async with client(app) as c:
        h = await admin(c)
        r = await c.post(
            "/api/volumes/p/local/v1/detach", json={"instance": "web", "device": "d"}, headers=h
        )
        assert r.json()["operation"] == {"id": "op9"}
    assert audit(services, 3)[2][2] is False


# --- copy and move (Phase 10a, D84; shapes as seen live on LXD 6.10) ---------------


@respx.mock
async def test_copy(app, services):
    respx.get(VOL).mock(
        return_value=lxd_ok({"name": "v1", "content_type": "block", "config": {"size": "1GiB"}})
    )
    create = respx.post(f"{POOLS}/remote/volumes/custom").mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "viewer", "viewerpw")}
        url = "/api/volumes/p/local/v1/copy"
        r = await c.post(url, json={"name": "v2", "pool": "remote"}, headers=h)
        assert r.status_code == 403
        h = await admin(c)
        new = {"name": "v2", "pool": "remote", "project": "q", "target": "m2", "volume_only": True}
        r = await c.post(url, params={"target": "m1"}, json=new, headers=h)
        assert r.status_code == 200, r.text
        assert body(create) == {
            "name": "v2",
            "type": "custom",
            "content_type": "block",
            "config": {"size": "1GiB"},
            "source": {
                "type": "copy",
                "name": "v1",
                "pool": "local",
                "project": "p",
                "volume_only": True,
                "location": "m1",
            },
        }
        assert params(create) == {"project": "q", "target": "m2"}
        assert audit(services)[0][:2] == ("volume.copy", "p/local/v1")
        assert audit(services)[0][3] == {"to": "q/remote/v2"}
    # no size (an unlimited project's volume): none sent; same project by default
    respx.get(VOL).mock(return_value=lxd_ok({"name": "v1", "config": {}}))
    async with client(app) as c:
        h = await admin(c)
        url = "/api/volumes/p/local/v1/copy"
        await c.post(url, json={"name": "v3", "pool": "remote"}, headers=h)
        assert body(create)["config"] == {} and body(create)["content_type"] == "filesystem"
        assert params(create) == {"project": "p"}
        assert "location" not in body(create)["source"]


@respx.mock
async def test_move(app, services):
    move = respx.post(VOL).mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = await admin(c)
        url = "/api/volumes/p/local/v1/move"
        await c.post(url, json={"project": "q"}, headers=h)
        assert body(move) == {"name": "v1", "pool": "local", "project": "q"}  # pool named anyway
        assert params(move) == {"project": "p"}
        await c.post(url, params={"target": "m1"}, json={"pool": "remote", "name": "v9"}, headers=h)
        assert body(move) == {"name": "v9", "pool": "remote"}
        assert params(move) == {"project": "p", "target": "m1"}
        assert audit(services)[0][3] == {"to": "p/remote/v9"}
        assert (await c.post(url, json={"project": "p"}, headers=h)).status_code == 400
        assert (await c.post(url, json={}, headers=h)).status_code == 400
