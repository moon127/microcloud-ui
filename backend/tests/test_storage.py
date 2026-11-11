import json

import pytest
import respx

from mcui.config import Settings
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import EP1, fast_store, fixture_json, lxd_err, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair
from tests.test_manage import admin, audit, body, params

POOLS = f"{EP1}/1.0/storage-pools"


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


RES = {"space": {"used": 10, "total": 100}, "inodes": {"used": 1, "total": 9}}


@respx.mock
async def test_detail_and_members(app):
    clustered()
    respx.get(f"{POOLS}/local", params={"target": "m1"}).mock(
        return_value=lxd_ok(
            {"status": "Created", "config": {"source": "/dev/sdb", "zfs.pool_name": "z", "x": "y"}}
        )
    )
    respx.get(f"{POOLS}/local/resources", params={"target": "m1"}).mock(return_value=lxd_ok(RES))
    respx.get(f"{POOLS}/local", params={"target": "m2"}).mock(
        return_value=lxd_err("Member unreachable", 503)
    )
    respx.get(f"{POOLS}/local/resources", params={"target": "m2"}).mock(return_value=lxd_ok(RES))
    get = respx.get(f"{POOLS}/local").mock(return_value=lxd_ok({"name": "local", "config": {}}))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get("/api/storage-pools/local")).json()["name"] == "local"
        assert params(get) == {}
        assert (await c.get("/api/storage-pools/local/members")).json() == [
            {
                "member": "m1",
                "config": {"source": "/dev/sdb", "zfs.pool_name": "z"},
                "status": "Created",
                "resources": RES,
            },
            {"member": "m2", "config": {}, "error": "Member unreachable"},
        ]


@respx.mock
async def test_members_standalone(app):
    standalone()
    respx.get(f"{POOLS}/default").mock(return_value=lxd_ok({"config": {"source": "/var/x"}}))
    respx.get(f"{POOLS}/default/resources").mock(return_value=lxd_ok(RES))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        [m] = (await c.get("/api/storage-pools/default/members")).json()
        md = fixture_json("server_standalone.json")
        assert m == {
            "member": md["environment"]["server_name"],
            "config": {"source": "/var/x"},
            "status": "",
            "resources": RES,
        }


@respx.mock
async def test_create_local_pool_in_a_cluster(app, services):
    clustered()
    post = respx.post(POOLS).mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "viewer", "viewerpw")}
        new = {"name": "fast", "driver": "zfs", "config": {"volume.zfs.remove_snapshots": "true"}}
        assert (await c.post("/api/storage-pools", json=new, headers=h)).status_code == 403
        h = await admin(c)
        r = await c.post(
            "/api/storage-pools",
            json={
                **new,
                "description": "nvme",
                "members": {"m1": {"source": "/dev/nvme0n1"}, "m2": {"size": "20GiB"}},
            },
            headers=h,
        )
        assert r.status_code == 200, r.text
    sent = [(dict(c.request.url.params), json.loads(c.request.content)) for c in post.calls]
    assert sent == [
        ({"target": "m1"}, {"name": "fast", "driver": "zfs", "config": {"source": "/dev/nvme0n1"}}),
        ({"target": "m2"}, {"name": "fast", "driver": "zfs", "config": {"size": "20GiB"}}),
        (
            {},
            {
                "name": "fast",
                "driver": "zfs",
                "description": "nvme",
                "config": {"volume.zfs.remove_snapshots": "true"},
            },
        ),
    ]
    assert audit(services)[0][:3] == ("storage-pool.create", "fast", True)


@respx.mock
async def test_create_remote_pool_and_rollback(app, services):
    clustered()
    post = respx.post(POOLS).mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = await admin(c)
        await c.post(
            "/api/storage-pools",
            json={"name": "rbd", "driver": "ceph", "config": {"ceph.osd.pool_name": "x"}},
            headers=h,
        )
        assert post.call_count == 1 and params(post) == {}
        r = await c.post(
            "/api/storage-pools",
            json={"name": "rbd", "driver": "ceph", "members": {"m1": {"source": "x"}}},
            headers=h,
        )
        assert r.status_code == 422
        r = await c.post(
            "/api/storage-pools",
            json={"name": "l", "driver": "lvm", "members": {"m1": {"lvm.use_thinpool": "x"}}},
            headers=h,
        )
        assert r.status_code == 422


@respx.mock
async def test_create_rollback(app, services):
    clustered()
    respx.post(POOLS, params={"target": "m1"}).mock(return_value=lxd_ok(None))
    respx.post(POOLS, params={"target": "m2"}).mock(
        return_value=lxd_err("Source path doesn't exist", 400)
    )
    dele = respx.delete(f"{POOLS}/d").mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = await admin(c)
        r = await c.post("/api/storage-pools", json={"name": "d", "driver": "dir"}, headers=h)
        assert r.status_code == 502 and "Source path" in r.text
    assert dele.call_count == 1
    assert audit(services)[0][2] is False


@respx.mock
async def test_create_standalone_merges_member_keys(app):
    standalone()
    post = respx.post(POOLS).mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = await admin(c)
        await c.post(
            "/api/storage-pools",
            json={"name": "b", "driver": "btrfs", "members": {"lxd1": {"size": "5GiB"}}},
            headers=h,
        )
    assert post.call_count == 1 and body(post)["config"] == {"size": "5GiB"}


@respx.mock
async def test_update_and_delete(app, services):
    put = respx.put(f"{POOLS}/p").mock(return_value=lxd_ok(None))
    respx.get(f"{POOLS}/p").mock(
        return_value=lxd_ok({"description": "keep", "config": {"volatile.pool.pristine": "true"}})
    )
    respx.delete(f"{POOLS}/p").mock(
        return_value=lxd_err("The storage pool is currently in use", 400)
    )
    async with client(app) as c:
        h = await admin(c)
        await c.put(
            "/api/storage-pools/p", json={"description": "d", "config": {"a": "1"}}, headers=h
        )
        assert params(put) == {}
        assert body(put) == {
            "description": "d",
            "config": {"a": "1", "volatile.pool.pristine": "true"},
        }
        await c.put(
            "/api/storage-pools/p",
            params={"target": "m1"},
            json={"config": {"size": "30GiB"}},
            headers=h,
        )
        assert body(put) == {"description": "keep", "config": {"size": "30GiB"}}
        assert params(put) == {"target": "m1"}
        r = await c.put(
            "/api/storage-pools/p", params={"target": "m1"}, json={"config": {"a": "1"}}, headers=h
        )
        assert r.status_code == 422
        r = await c.delete("/api/storage-pools/p", headers=h)
        assert r.status_code == 502 and "in use" in r.text
    assert [a[0] for a in audit(services, 3)] == [
        "storage-pool.delete",
        "storage-pool.update",
        "storage-pool.update",
    ]
    assert audit(services, 2)[1][3] == {"target": "m1"}
