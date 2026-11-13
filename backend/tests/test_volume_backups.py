"""Custom volume backups (Phase 7b, D83)."""

import httpx
import respx

from tests.conftest import EP1, lxd_err, lxd_ok
from tests.test_api import CSRF_HEADER, client, login
from tests.test_api_instances import admin, app, async_resp, body, last_audit, services

__all__ = ["app", "services"]  # fixtures

VOLS = f"{EP1}/1.0/storage-pools/local/volumes/custom"
B = f"{VOLS}/v1/backups"
U = "/api/volumes/p/local/v1/backups"


@respx.mock
async def test_list_on_a_member(app):
    route = respx.get(B).mock(
        return_value=lxd_ok(
            [{"name": "a", "created_at": "2026-01-01"}, {"name": "b", "created_at": "2026-02-01"}]
        )
    )
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        r = await c.get(U, params={"target": "m2"})
    assert [b["name"] for b in r.json()] == ["b", "a"]
    assert dict(route.calls.last.request.url.params) == {
        "project": "p",
        "target": "m2",
        "recursion": "1",
    }


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
        new = {"name": "b1", "volume_only": True, "compression_algorithm": "xz"}
        r = await c.post(U, json=new, headers=h, params={"target": "m1"})
        assert r.status_code == 200
        assert body(create) == {**new, "optimized_storage": False}
        assert create.calls.last.request.url.params["target"] == "m1"
        assert last_audit(services)[:2] == ("volume.backup.create", "p/local/v1")
        await c.post(f"{U}/b1/rename", json={"name": "b2"}, headers=h)
        assert body(rename) == {"name": "b2"}
        assert last_audit(services)[4] == {"backup": "b1", "to": "b2"}
        await c.delete(f"{U}/b1", headers=h)
        assert delete.called and last_audit(services)[0] == "volume.backup.delete"


@respx.mock
async def test_export(app, services):
    data = b"\x28\xb5\x2f\xfd" + b"\x00" * 1000
    respx.get(f"{B}/b1/export", params={"project": "p"}).mock(
        return_value=httpx.Response(200, content=data)
    )
    respx.get(f"{B}/b9/export").mock(return_value=lxd_err("Backup not found", 404))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get(f"{U}/b1/export")).status_code == 403
        await admin(c)
        r = await c.get(f"{U}/b1/export")
        assert r.content == data
        assert r.headers["content-disposition"] == 'attachment; filename="v1-b1.tar.zst"'
        assert last_audit(services)[0] == "volume.backup.export"
        n = len(services.store.audit_entries(limit=100))
        assert (await c.get(f"{U}/b9/export")).status_code == 404
        # nothing exported: nothing audited
        assert len(services.store.audit_entries(limit=100)) == n


@respx.mock
async def test_import(app, services):
    imp = respx.post(VOLS).mock(return_value=async_resp())
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "viewer", "viewerpw")}
        q = {"project": "p", "pool": "local", "name": "v2", "target": "m3"}
        url = "/api/volumes/import"
        assert (await c.post(url, params=q, content=b"x", headers=h)).status_code == 403
        h = await admin(c)
        r = await c.post(url, params=q, content=b"tarball", headers=h)
        assert r.status_code == 200, r.text
        req = imp.calls.last.request
        assert req.content == b"tarball" and req.headers["x-lxd-name"] == "v2"
        assert dict(req.url.params) == {"project": "p", "target": "m3"}
        assert last_audit(services)[:2] == ("volume.backup.import", "p/local/v2")
        await c.post(url, params={"pool": "local"}, content=b"t", headers=h)
        req = imp.calls.last.request
        assert "x-lxd-name" not in req.headers and req.url.params["project"] == "default"
        r = await c.post(url, params={"pool": "local"}, content=b"", headers=h)
        assert r.status_code == 400
        assert (await c.post(url, content=b"t", headers=h)).status_code == 422  # no pool


@respx.mock
async def test_iso_upload(app, services):
    up = respx.post(VOLS).mock(return_value=async_resp())
    url = "/api/volumes/iso"
    q = {"project": "p", "pool": "local", "name": "ubuntu-iso", "target": "m1"}
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "viewer", "viewerpw")}
        assert (await c.post(url, params=q, content=b"x", headers=h)).status_code == 403
        h = await admin(c)
        r = await c.post(url, params=q, content=b"CD001", headers=h)
        assert r.status_code == 200, r.text
        req = up.calls.last.request
        assert req.content == b"CD001" and req.headers["x-lxd-type"] == "iso"
        assert req.headers["x-lxd-name"] == "ubuntu-iso"
        assert dict(req.url.params) == {"project": "p", "target": "m1"}
        assert last_audit(services)[:2] == ("volume.iso.upload", "p/local/ubuntu-iso")
        no_name = {"project": "p", "pool": "local"}
        assert (await c.post(url, params=no_name, content=b"x", headers=h)).status_code == 422
        assert (await c.post(url, params=q, content=b"", headers=h)).status_code == 400
