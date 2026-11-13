"""Instance backups (Phase 7a, D83)."""

import httpx
import pytest
import respx

from mcui.api.backups import suffix
from tests.conftest import lxd_err, lxd_ok
from tests.test_api import CSRF_HEADER, client, login
from tests.test_api_instances import INST, admin, app, async_resp, body, last_audit, services

__all__ = ["app", "services"]  # fixtures

B = f"{INST}/c1/backups"
U = "/api/instances/p/c1/backups"


@pytest.mark.parametrize(
    ("head", "ext"),
    [
        (b"\x1f\x8b\x08", ".tar.gz"),
        (b"\x28\xb5\x2f\xfd", ".tar.zst"),
        (b"\xfd7zXZ\x00", ".tar.xz"),
        (b"BZh9", ".tar.bz2"),
        (b"backup/", ".tar"),
        (b"", ".tar"),
    ],
)
def test_suffix(head, ext):
    assert suffix(head) == ext


@respx.mock
async def test_list_newest_first(app):
    respx.get(B, params={"project": "p", "recursion": "1"}).mock(
        return_value=lxd_ok(
            [
                {"name": "old", "created_at": "2026-01-01"},
                {"name": "new", "created_at": "2026-02-01"},
            ]
        )
    )
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")  # listing is read
        r = await c.get(U)
    assert [b["name"] for b in r.json()] == ["new", "old"]


@respx.mock
async def test_list_none(app):
    respx.get(B).mock(return_value=lxd_ok(None))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get(U)).json() == []


@respx.mock
async def test_create_rename_delete(app, services):
    create = respx.post(B).mock(return_value=async_resp())
    rename = respx.post(f"{B}/b1").mock(return_value=async_resp())
    delete = respx.delete(f"{B}/b1").mock(return_value=async_resp())
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "viewer", "viewerpw")}
        assert (await c.post(U, json={"name": "b1"}, headers=h)).status_code == 403
        h = await admin(c)
        new = {"name": "b1", "instance_only": True, "compression_algorithm": "zstd"}
        assert (await c.post(U, json=new, headers=h)).status_code == 200
        assert body(create) == {**new, "optimized_storage": False}
        assert create.calls.last.request.url.params["project"] == "p"
        assert last_audit(services)[:2] == ("backup.create", "p/c1")
        bad = {"name": "b1", "compression_algorithm": "lz4"}
        assert (await c.post(U, json=bad, headers=h)).status_code == 422
        await c.post(f"{U}/b1/rename", json={"name": "b2"}, headers=h)
        assert body(rename) == {"name": "b2"}
        assert last_audit(services)[4] == {"backup": "b1", "to": "b2"}
        await c.delete(f"{U}/b1", headers=h)
        assert delete.called and last_audit(services)[0] == "backup.delete"


@respx.mock
async def test_export_streams_with_a_name(app, services):
    data = b"\x1f\x8b" + b"\x00" * 70000
    respx.get(f"{B}/b1/export", params={"project": "p"}).mock(
        return_value=httpx.Response(200, content=data)
    )
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get(f"{U}/b1/export")).status_code == 403  # it holds the files
        await admin(c)
        r = await c.get(f"{U}/b1/export")
    assert r.status_code == 200 and r.content == data
    assert r.headers["content-disposition"] == 'attachment; filename="c1-b1.tar.gz"'
    assert r.headers["content-length"] == str(len(data))
    assert last_audit(services)[0] == "backup.export"


@respx.mock
async def test_export_without_length(app):
    def chunked(_req):
        r = httpx.Response(200, content=b"backup/index.yaml")
        del r.headers["content-length"]
        return r

    respx.get(f"{B}/b1/export").mock(side_effect=chunked)
    async with client(app) as c:
        await admin(c)
        r = await c.get(f"{U}/b1/export")
    assert r.headers["content-disposition"].endswith('c1-b1.tar"')
    assert "content-length" not in r.headers


@respx.mock
async def test_export_missing(app):
    respx.get(f"{B}/b9/export").mock(return_value=lxd_err("Backup not found", 404))
    async with client(app) as c:
        await admin(c)
        r = await c.get(f"{U}/b9/export")
    assert r.status_code == 404 and "Backup not found" in r.json()["detail"]


@respx.mock
async def test_import(app, services):
    imp = respx.post(INST, params={"project": "p"}).mock(return_value=async_resp())
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "viewer", "viewerpw")}
        q = {"project": "p", "name": "c2", "pool": "remote"}
        assert (
            await c.post("/api/instances/import", params=q, content=b"x", headers=h)
        ).status_code == 403
        h = await admin(c)
        r = await c.post("/api/instances/import", params=q, content=b"tarball", headers=h)
        assert r.status_code == 200, r.text
        req = imp.calls.last.request
        assert req.content == b"tarball"
        assert req.headers["x-lxd-name"] == "c2" and req.headers["x-lxd-pool"] == "remote"
        assert req.headers["content-type"] == "application/octet-stream"
        assert last_audit(services)[:2] == ("backup.import", "p/c2")
        # no overrides: the backup's own name and pool
        await c.post("/api/instances/import", params={"project": "p"}, content=b"t", headers=h)
        req = imp.calls.last.request
        assert "x-lxd-name" not in req.headers and "x-lxd-pool" not in req.headers
        assert last_audit(services)[1] == "p/(from backup)"
        r = await c.post("/api/instances/import", params={"project": "p"}, content=b"", headers=h)
        assert r.status_code == 400
        imp.mock(return_value=lxd_err('Project "p" does not allow for backup creation', 403))
        r = await c.post("/api/instances/import", params={"project": "p"}, content=b"t", headers=h)
        assert r.status_code == 502 and "does not allow" in r.json()["detail"]
        bad = {"project": "p", "name": "../x"}
        r = await c.post("/api/instances/import", params=bad, content=b"t", headers=h)
        assert r.status_code == 422
