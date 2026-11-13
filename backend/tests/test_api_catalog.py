import json
from pathlib import Path

import httpx
import pytest
import respx

from mcui.config import Settings
from mcui.lxd.remotes import RemoteCatalog
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import EP1, fast_store, fixture_json, lxd_err, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair
from tests.test_remotes import INDEX, PRODUCTS

RECORDED = Path(__file__).parent / "fixtures/recorded-6.10"


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
    nets = respx.get(f"{EP1}/1.0/networks").mock(
        return_value=lxd_ok(
            [{"name": "ovn1", "type": "ovn", "managed": True, "used_by": ["/1.0/instances/a"]}]
        )
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
        listed = (await c.get("/api/networks")).json()[0]
        assert listed["managed"] is True and listed["used_by"] == 1 and listed["locations"] == []
        await c.get("/api/networks", params={"target": "m2"})
        assert nets.calls.last.request.url.params["target"] == "m2"


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
    # LXD 5.21: no image registries, so the simplestreams server is named
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(fixture_json("server_clustered.json")))
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

        # an empty VM, to install from an ISO (Phase 7d)
        await c.post("/api/instances", json={"name": "e", "type": "virtual-machine"}, headers=h)
        assert json.loads(route.calls.last.request.content)["source"] == {"type": "none"}
        assert services.store.audit_entries(limit=1)[0]["detail"]["image"] == "(empty)"

        for bad, code in [
            ({"name": "c"}, 400),  # a container needs an image
            ({"name": "c", "image": {}}, 400),
            ({"name": "c", "image": {"remote": "ubuntu"}}, 400),
            ({"name": "bad name", "image": {"alias": "x"}}, 422),
            ({"name": "c", "target": "m1/../x", "image": {"alias": "x"}}, 422),
            ({"name": "c", "image": {"remote": "http://evil", "alias": "x"}}, 422),
            ({"name": "c", "type": "lxc", "image": {"alias": "x"}}, 422),
        ]:
            assert (await c.post("/api/instances", json=bad, headers=h)).status_code == code, bad


def lxd610():
    md = fixture_json("server_clustered.json")
    md["api_extensions"] = [*md["api_extensions"], "image_registries"]
    md["environment"]["architectures"] = ["x86_64"]
    return md


@respx.mock
async def test_remote_images_from_lxd_registries(app, services):
    """LXD 6.10 lists a registry's images itself (Phase 5b); recorded on the 6.10 VM."""
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(lxd610()))
    rec = RECORDED / "image-registry_ubuntu_images_slice.json"
    reg = respx.get(f"{EP1}/1.0/image-registries/ubuntu/images").mock(
        return_value=lxd_ok(json.loads(rec.read_text()))
    )
    respx.get(f"{EP1}/1.0/image-registries/nope/images").mock(
        return_value=lxd_err('Failed loading image registry "nope": not found', 404)
    )
    respx.get(f"{EP1}/1.0/image-registries/broken/images").mock(
        return_value=lxd_err("upstream timeout", 500)
    )
    async with client(app) as c:
        await login(c, "admin", "adminpw")
        r = (await c.get("/api/images/remote/ubuntu")).json()
        assert [(i["alias"], i["container"], i["vm"]) for i in r] == [
            ("24.04", True, True),
            ("26.04", True, True),
        ]  # newest serial per product, amd64 only (the cluster's architecture)
        await c.get("/api/images/remote/ubuntu")
        assert reg.call_count == 1  # cached: LXD takes seconds for big registries
        assert (await c.get("/api/images/remote/nope")).status_code == 404
        assert (await c.get("/api/images/remote/broken")).status_code == 502
        assert (await c.get("/api/images/remote/bad name")).status_code == 404


@respx.mock
async def test_create_instance_from_a_registry(app, services):
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(lxd610()))
    route = respx.post(f"{EP1}/1.0/instances").mock(
        return_value=httpx.Response(202, json={"type": "async", "metadata": {"id": "op9"}})
    )
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        body = {"name": "c1", "image": {"remote": "lab-mirror", "alias": "alpine/edge"}}
        await c.post("/api/instances", json=body, headers=h)
        assert json.loads(route.calls.last.request.content)["source"] == {
            "type": "image",
            "alias": "alpine/edge",
            "image_registry": "lab-mirror",
        }


@respx.mock
async def test_unknown_remote_on_lxd_5_21(app, services):
    respx.get(f"{EP1}/1.0").mock(return_value=lxd_ok(fixture_json("server_clustered.json")))
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        body = {"name": "c1", "image": {"remote": "lab-mirror", "alias": "alpine/edge"}}
        r = await c.post("/api/instances", json=body, headers=h)
        assert r.status_code == 400 and "unknown remote" in r.text
