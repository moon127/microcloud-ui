"""Image registries API (LXD 6.10+, Phase 5b)."""

import json

import pytest
import respx

from mcui.config import Settings
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import EP1, fast_store, lxd_err, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair
from tests.test_api_ops import actions, async_resp


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
async def test_list_add_delete(app, services):
    respx.get(f"{EP1}/1.0/image-registries").mock(
        return_value=lxd_ok(
            [  # as LXD 6.10 lists them
                {"name": "images", "builtin": True, "protocol": "simplestreams", "config": {}},
                {"name": "lab-mirror", "builtin": False, "protocol": "simplestreams"},
            ]
        )
    )
    add = respx.post(f"{EP1}/1.0/image-registries").mock(return_value=async_resp())
    delete = respx.delete(f"{EP1}/1.0/image-registries/lab-mirror").mock(return_value=async_resp())
    respx.delete(f"{EP1}/1.0/image-registries/ubuntu").mock(
        return_value=lxd_err("Built-in image registry cannot be deleted", 400)
    )
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        r = await c.get("/api/image-registries")
        assert [x["name"] for x in r.json()] == ["images", "lab-mirror"]
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        body = {"name": "mirror", "url": "https://images.example.org", "description": "d"}
        await c.post("/api/image-registries", json=body, headers=h)
        assert json.loads(add.calls.last.request.content) == {
            "name": "mirror",
            "description": "d",
            "config": {"url": "https://images.example.org"},
        }
        for bad in ("http://images.example.org", "ftp://x", "https://"):
            r = await c.post("/api/image-registries", json={"name": "m", "url": bad}, headers=h)
            assert r.status_code == 422, bad  # LXD: "URL must be HTTPS"
        await c.delete("/api/image-registries/lab-mirror", headers=h)
        assert delete.called
        r = await c.delete("/api/image-registries/ubuntu", headers=h)
        assert r.status_code == 502 and "cannot be deleted" in r.text
    assert [a[:3] for a in actions(services)[:3]] == [
        ("image-registry.delete", "ubuntu", False),
        ("image-registry.delete", "lab-mirror", True),
        ("image-registry.create", "mirror", True),
    ]


async def test_viewer_cannot_add(app):
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "viewer", "viewerpw")}
        r = await c.post(
            "/api/image-registries", json={"name": "m", "url": "https://x.org"}, headers=h
        )
        assert r.status_code == 403
