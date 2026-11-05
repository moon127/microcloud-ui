import json

import httpx
import pytest
import respx

from mcui.config import Settings
from mcui.lxd.remotes import RemoteCatalog
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import EP1, fast_store, fixture_json, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair
from tests.test_remotes import INDEX, PRODUCTS


@pytest.fixture
def services(tmp_path):
    store = fast_store()
    store.add_user("admin", "adminpw", "admin")
    settings = Settings(server={"data_dir": str(tmp_path), "secure_cookies": False})
    svc = build_services(settings, store=store, clients=lxd_pair())

    def handler(req):
        return httpx.Response(200, json=INDEX if req.url.path.endswith("index.json") else PRODUCTS)

    svc.remotes = RemoteCatalog(transport=httpx.MockTransport(handler))
    return svc


@pytest.fixture
def app(services):
    return create_app(services=services, static_dir=None)


@respx.mock
async def test_local_images_profiles_networks(app):
    img = respx.get(f"{EP1}/1.0/images").mock(
        return_value=lxd_ok(
            [
                {
                    "fingerprint": "abc",
                    "aliases": [{"name": "alpine"}],
                    "properties": {"description": "Alpine edge"},
                    "type": "container",
                    "architecture": "x86_64",
                    "size": 3,
                },
                {"fingerprint": "def"},
            ]
        )
    )
    respx.get(f"{EP1}/1.0/profiles").mock(
        return_value=lxd_ok([{"name": "default", "devices": {"root": {}}}])
    )
    respx.get(f"{EP1}/1.0/networks").mock(
        return_value=lxd_ok([{"name": "ovn1", "type": "ovn", "managed": True}])
    )
    async with client(app) as c:
        await login(c, "admin", "adminpw")
        images = (await c.get("/api/images", params={"project": "mcui-test"})).json()
        assert img.calls.last.request.url.params["project"] == "mcui-test"
        assert images[0]["aliases"] == ["alpine"] and images[0]["description"] == "Alpine edge"
        assert images[1] == {
            "fingerprint": "def",
            "aliases": [],
            "description": "",
            "type": "",
            "architecture": "",
            "size": 0,
            "created_at": "",
            "uploaded_at": "",
            "last_used_at": "",
            "cached": False,
            "public": False,
            "auto_update": False,
            "properties": {},
            "project": "mcui-test",
        }
        assert (await c.get("/api/profiles")).json() == [
            {
                "name": "default",
                "description": "",
                "config": {},
                "devices": {"root": {}},
                "used_by": [],
                "project": "default",
            }
        ]
        assert (await c.get("/api/networks")).json()[0]["managed"] is True


@respx.mock
async def test_remote_images(app, services):
    md = fixture_json("server_clustered.json")
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(md))
    async with client(app) as c:
        await login(c, "admin", "adminpw")
        r = (await c.get("/api/images/remote/images")).json()
        assert [i["alias"] for i in r] == ["alpine/edge"]  # x86_64/i686 cluster: amd64 only
        r = (await c.get("/api/images/remote/ubuntu", params={"arch": "arm64"})).json()
        assert [i["alias"] for i in r] == ["24.04"]
        assert (await c.get("/api/images/remote/evil")).status_code == 404
        md["environment"]["architectures"] = []
        respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(md))
        assert len((await c.get("/api/images/remote/images")).json()) == 2  # unknown arch: all

        async def boom(remote):
            raise httpx.ConnectError("offline")

        services.remotes.images = boom
        r = await c.get("/api/images/remote/images", params={"arch": "amd64"})
        assert r.status_code == 502 and "ConnectError" in r.json()["detail"]


@respx.mock
async def test_create_instance(app, services):
    route = respx.post(f"{EP1}/1.0/instances").mock(
        return_value=httpx.Response(
            202, json={"type": "async", "metadata": {"id": "op9", "status": "Running"}}
        )
    )
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        r = await c.post(
            "/api/instances",
            json={
                "name": "web1",
                "project": "mcui-test",
                "target": "@gpu",
                "image": {"remote": "images", "alias": "alpine/edge"},
                "config": {"limits.cpu": "1"},
                "devices": {
                    "root": {"type": "disk", "path": "/", "pool": "remote", "size": "2GiB"}
                },
            },
            headers=h,
        )
        assert r.json()["operation"]["id"] == "op9"
        req = route.calls.last.request
        assert dict(req.url.params) == {"project": "mcui-test", "target": "@gpu"}
        assert json.loads(req.content) == {
            "name": "web1",
            "type": "container",
            "description": "",
            "profiles": ["default"],
            "config": {"limits.cpu": "1"},
            "devices": {"root": {"type": "disk", "path": "/", "pool": "remote", "size": "2GiB"}},
            "start": True,
            "source": {
                "type": "image",
                "alias": "alpine/edge",
                "server": "https://images.lxd.canonical.com",
                "protocol": "simplestreams",
                "mode": "pull",
            },
        }
        e = services.store.audit_entries(limit=1)[0]
        assert (e["action"], e["target"], e["operation_id"]) == (
            "instance.create",
            "mcui-test/web1",
            "op9",
        )
        assert e["detail"] == {"image": "images:alpine/edge", "type": "container"}

        await c.post(
            "/api/instances",
            json={"name": "a", "image": {"fingerprint": "abcdef012345"}, "start": False},
            headers=h,
        )
        assert json.loads(route.calls.last.request.content)["source"] == {
            "type": "image",
            "fingerprint": "abcdef012345",
        }
        assert dict(route.calls.last.request.url.params) == {"project": "default"}
        await c.post(
            "/api/instances",
            json={"name": "b", "type": "virtual-machine", "image": {"alias": "golden"}},
            headers=h,
        )
        assert json.loads(route.calls.last.request.content)["source"] == {
            "type": "image",
            "alias": "golden",
        }

        for bad, code in [
            ({"name": "c", "image": {}}, 400),
            ({"name": "c", "image": {"remote": "ubuntu"}}, 400),
            ({"name": "bad name", "image": {"alias": "x"}}, 422),
            ({"name": "c", "target": "m1/../x", "image": {"alias": "x"}}, 422),
            ({"name": "c", "image": {"remote": "http://evil", "alias": "x"}}, 422),
            ({"name": "c", "type": "lxc", "image": {"alias": "x"}}, 422),
        ]:
            assert (await c.post("/api/instances", json=bad, headers=h)).status_code == code, bad
