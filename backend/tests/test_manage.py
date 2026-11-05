import json

import httpx
import pytest
import respx

from mcui.config import Settings
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import EP1, fast_store, lxd_err, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair

FP = "0123456789abcdef" * 4
OP = {"id": "op1", "status": "Running"}


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


async def admin(c):
    return {CSRF_HEADER: await login(c, "admin", "adminpw")}


def body(route):
    return json.loads(route.calls.last.request.content)


def params(route):
    return dict(route.calls.last.request.url.params)


def audit(services, n=1):
    return [
        (e["action"], e["target"], e["ok"], e["detail"])
        for e in services.store.audit_entries(limit=n)
    ]


@respx.mock
async def test_profiles(app, services):
    get = respx.get(f"{EP1}/1.0/profiles/web").mock(
        return_value=lxd_ok({"name": "web", "used_by": []})
    )
    create = respx.post(f"{EP1}/1.0/profiles").mock(return_value=lxd_ok(None))
    put = respx.put(f"{EP1}/1.0/profiles/web").mock(return_value=lxd_ok(None))
    ren = respx.post(f"{EP1}/1.0/profiles/web").mock(return_value=lxd_ok(None))
    dele = respx.delete(f"{EP1}/1.0/profiles/web").mock(
        return_value=lxd_err("Profile is currently in use", 400)
    )
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "viewer", "viewerpw")}
        assert (await c.get("/api/profiles/p/web")).json()["name"] == "web" and params(get) == {
            "project": "p"
        }
        assert (await c.post("/api/profiles", json={"name": "x"}, headers=h)).status_code == 403
        h = await admin(c)
        await c.post(
            "/api/profiles",
            json={"name": "web", "project": "p", "config": {"limits.cpu": "2"}},
            headers=h,
        )
        assert body(create) == {
            "description": "",
            "config": {"limits.cpu": "2"},
            "devices": {},
            "name": "web",
        }
        assert params(create) == {"project": "p"}
        await c.put("/api/profiles/p/web", json={"devices": {"root": {"type": "disk"}}}, headers=h)
        assert body(put) == {"description": "", "config": {}, "devices": {"root": {"type": "disk"}}}
        await c.post("/api/profiles/p/web/rename", json={"name": "www"}, headers=h)
        assert body(ren) == {"name": "www"}
        r = await c.delete("/api/profiles/p/web", headers=h)
        assert r.status_code == 502 and "in use" in r.json()["detail"] and dele.called
        assert (await c.get("/api/profiles/p/bad%2Fname")).status_code in (404, 422)
    assert audit(services, 4) == [
        ("profile.delete", "p/web", False, {"error": "Profile is currently in use"}),
        ("profile.rename", "p/web", True, {"to": "www"}),
        ("profile.update", "p/web", True, None),
        ("profile.create", "p/web", True, None),
    ]


@respx.mock
async def test_images(app, services):
    respx.get(f"{EP1}/1.0/images/{FP}").mock(return_value=lxd_ok({"fingerprint": FP}))
    put = respx.put(f"{EP1}/1.0/images/{FP}").mock(return_value=lxd_ok(None))
    dele = respx.delete(f"{EP1}/1.0/images/{FP}").mock(return_value=async_resp())
    alias = respx.post(f"{EP1}/1.0/images/aliases").mock(return_value=lxd_ok(None))
    unalias = respx.delete(f"{EP1}/1.0/images/aliases/alpine/edge").mock(return_value=lxd_ok(None))
    imp = respx.post(f"{EP1}/1.0/images").mock(return_value=async_resp())
    async with client(app) as c:
        h = await admin(c)
        assert (await c.get(f"/api/images/p/{FP}")).json()["fingerprint"] == FP
        assert (await c.get("/api/images/p/NOTHEX")).status_code == 422
        await c.put(
            f"/api/images/p/{FP}",
            json={"properties": {"description": "x"}, "auto_update": True},
            headers=h,
        )
        assert body(put) == {
            "properties": {"description": "x"},
            "public": False,
            "auto_update": True,
        }
        assert (await c.delete(f"/api/images/p/{FP}", headers=h)).json()["operation"][
            "id"
        ] == "op1" and dele.called
        await c.post(f"/api/images/p/{FP}/aliases", json={"name": "golden"}, headers=h)
        assert body(alias) == {"name": "golden", "target": FP, "description": ""}
        await c.delete("/api/images/p/aliases/alpine/edge", headers=h)
        assert unalias.called and params(unalias) == {"project": "p"}
        await c.post(
            "/api/images/import",
            json={
                "project": "p",
                "remote": "images",
                "alias": "debian/12",
                "local_alias": "deb",
                "vm": True,
            },
            headers=h,
        )
        assert body(imp) == {
            "source": {
                "type": "image",
                "mode": "pull",
                "server": "https://images.lxd.canonical.com",
                "protocol": "simplestreams",
                "alias": "debian/12",
                "image_type": "virtual-machine",
            },
            "auto_update": True,
            "aliases": [{"name": "deb"}],
        }
        await c.post(
            "/api/images/import",
            json={"remote": "ubuntu", "alias": "24.04", "auto_update": False},
            headers=h,
        )
        assert body(imp) == {
            "source": {
                "type": "image",
                "mode": "pull",
                "server": "https://cloud-images.ubuntu.com/releases",
                "protocol": "simplestreams",
                "alias": "24.04",
            },
            "auto_update": False,
        }
        r = await c.post(
            "/api/images/import", json={"remote": "https://evil", "alias": "x"}, headers=h
        )
        assert r.status_code == 400
    assert audit(services, 1)[0][:2] == ("image.import", "default/ubuntu:24.04")


@respx.mock
async def test_image_upload_with_alias(app, services):
    up = respx.post(f"{EP1}/1.0/images").mock(return_value=async_resp())
    wait = respx.get(f"{EP1}/1.0/operations/op1/wait")
    wait.side_effect = [
        lxd_ok({**OP, "status": "Running"}),
        lxd_ok({**OP, "status": "Success", "metadata": {"fingerprint": FP}}),
    ]
    alias = respx.post(f"{EP1}/1.0/images/aliases").mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = await admin(c)
        r = await c.post(
            "/api/images/upload",
            params={"project": "p", "alias": "mine", "filename": "x.tar.gz"},
            content=b"TARBALL",
            headers=h,
        )
        assert r.json()["operation"]["status"] == "Success"
        req = up.calls.last.request
        assert req.content == b"TARBALL" and req.headers["x-lxd-filename"] == "x.tar.gz"
        assert body(alias) == {"name": "mine", "target": FP}
    assert [a[0] for a in audit(services, 2)] == ["image.alias.create", "image.upload"]


@respx.mock
async def test_image_upload_variants(app, services):
    up = respx.post(f"{EP1}/1.0/images")
    respx.get(f"{EP1}/1.0/operations/op1/wait").mock(
        return_value=lxd_ok({**OP, "status": "Failure", "err": "bad tarball"})
    )
    alias = respx.post(f"{EP1}/1.0/images/aliases").mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = await admin(c)
        up.mock(return_value=async_resp())
        r = await c.post("/api/images/upload", params={"alias": "a"}, content=b"x", headers=h)
        assert r.json()["operation"]["status"] == "Failure" and not alias.called  # failed: no alias
        up.mock(return_value=lxd_ok({"x": 1}))  # sync answer: no operation to follow
        assert (await c.post("/api/images/upload", content=b"x", headers=h)).json()[
            "operation"
        ] is None
        up.mock(return_value=lxd_err("Image already exists", 409))
        r = await c.post("/api/images/upload", content=b"x", headers=h)
        assert r.status_code == 502 and "already exists" in r.json()["detail"]
        up.mock(return_value=httpx.Response(500, text="boom"))
        r = await c.post("/api/images/upload", content=b"x", headers=h)
        assert r.status_code == 502 and "HTTP 500" in r.json()["detail"]
    assert audit(services, 1)[0][2] is False


@respx.mock
async def test_projects(app, services):
    respx.get(f"{EP1}/1.0/projects/lab").mock(return_value=lxd_ok({"name": "lab"}))
    create = respx.post(f"{EP1}/1.0/projects").mock(return_value=lxd_ok(None))
    put = respx.put(f"{EP1}/1.0/projects/lab").mock(return_value=lxd_ok(None))
    ren = respx.post(f"{EP1}/1.0/projects/lab").mock(return_value=async_resp())
    dele = respx.delete(f"{EP1}/1.0/projects/lab").mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = await admin(c)
        assert (await c.get("/api/projects/lab")).json() == {"name": "lab"}
        await c.post(
            "/api/projects", json={"name": "lab", "config": {"features.images": "true"}}, headers=h
        )
        assert body(create) == {
            "description": "",
            "config": {"features.images": "true"},
            "name": "lab",
        }
        await c.put(
            "/api/projects/lab",
            json={"description": "d", "config": {"limits.instances": "5"}},
            headers=h,
        )
        assert body(put) == {"description": "d", "config": {"limits.instances": "5"}}
        await c.post("/api/projects/lab/rename", json={"name": "lab2"}, headers=h)
        assert body(ren) == {"name": "lab2"}
        assert (await c.delete("/api/projects/lab", headers=h)).status_code == 200 and dele.called
        for r in (
            await c.post("/api/projects/default/rename", json={"name": "x"}, headers=h),
            await c.delete("/api/projects/default", headers=h),
        ):
            assert r.status_code == 400 and "default project" in r.json()["detail"]
    assert audit(services, 4)[0][:2] == ("project.delete", "lab")
