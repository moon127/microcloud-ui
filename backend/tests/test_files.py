import httpx
import pytest
import respx

from mcui.api.files import MAX_ENTRIES, clean
from mcui.config import Settings
from mcui.lxd.client import LXDUnavailable
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import EP1, fast_store, lxd_err, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair

F = f"{EP1}/1.0/instances/c1/files"
U = "/api/instances/p/c1/files"


def head(kind, size=None, mode="0644"):
    h = {
        "x-lxd-type": kind,
        "x-lxd-mode": mode,
        "x-lxd-uid": "0",
        "x-lxd-gid": "0",
        "x-lxd-modified": "t",
    }
    if size is not None:
        h["content-length"] = str(size)
    return h


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


def by_path(responses):
    """respx side effect choosing a response by ?path= (and method)."""

    def handler(request):
        return responses[(request.method, request.url.params["path"])]

    return handler


def test_clean():
    assert clean("/etc/../root//x/") == "/root/x"
    assert clean("/") == "/"
    for bad in ("etc", "/a\x00b"):
        with pytest.raises(Exception, match="400"):
            clean(bad)


@respx.mock
async def test_browse_directory_symlink_and_file(app):
    respx.route(url__startswith=F).mock(
        side_effect=by_path(
            {
                ("GET", "/etc"): httpx.Response(
                    200,
                    json={"type": "sync", "metadata": ["b", "a", "gone"]},
                    headers=head("directory", mode="0755"),
                ),
                ("HEAD", "/etc/a"): httpx.Response(200, headers=head("file", 12)),
                ("HEAD", "/etc/b"): httpx.Response(200, headers=head("directory")),
                ("HEAD", "/etc/gone"): httpx.Response(404),
                ("GET", "/bin/sh"): httpx.Response(
                    200, content=b"../usr/bin/busybox", headers=head("symlink")
                ),
                ("GET", "/etc/hostname"): httpx.Response(
                    200, content=b"c1\n", headers=head("file")
                ),
                ("GET", "/etc/sized"): httpx.Response(
                    200, content=b"", headers={**head("file"), "content-length": "0"}
                ),
                ("GET", "/missing"): lxd_err("not found", 404),
                ("GET", "/broken"): httpx.Response(500, text="<html>"),
            }
        )
    )
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        d = (await c.get(U, params={"path": "/etc/"})).json()
        assert d["path"] == "/etc" and d["type"] == "directory" and d["truncated"] is False
        assert [(e["name"], e["type"]) for e in d["entries"]] == [
            ("b", "directory"),
            ("a", "file"),
            ("gone", "unknown"),
        ]
        assert d["entries"][1]["size"] == 12 and d["entries"][1]["mode"] == "0644"
        s = (await c.get(U, params={"path": "/bin/sh"})).json()
        assert (s["type"], s["target"], s["resolved"]) == (
            "symlink",
            "../usr/bin/busybox",
            "/usr/bin/busybox",
        )
        f = (await c.get(U, params={"path": "/etc/hostname"})).json()
        assert (f["type"], f["size"]) == ("file", 3)
        assert (await c.get(U, params={"path": "/etc/sized"})).json()["size"] == 0
        r = await c.get(U, params={"path": "/missing"})
        assert r.status_code == 404 and r.json()["detail"] == "LXD: not found"
        r = await c.get(U, params={"path": "/broken"})
        assert r.status_code == 502 and r.json()["detail"] == "LXD: HTTP 500"
        assert (await c.get(U, params={"path": "relative"})).status_code == 400


@respx.mock
async def test_large_directory_is_truncated(app, monkeypatch):
    monkeypatch.setattr("mcui.api.files.MAX_ENTRIES", 2)
    respx.route(url__startswith=F).mock(
        side_effect=lambda req: (
            httpx.Response(200, json={"metadata": ["c", "a", "b"]}, headers=head("directory"))
            if req.method == "GET"
            else httpx.Response(200, headers=head("file", 1))
        )
    )
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        d = (await c.get(U, params={"path": "/"})).json()
    assert d["truncated"] is True and [(e["name"], e["type"]) for e in d["entries"]] == [
        ("a", "file"),
        ("b", "file"),
        ("c", "unknown"),
    ]
    assert MAX_ENTRIES == 500


def no_length(content):
    r = httpx.Response(200, content=content, headers=head("file"))
    del r.headers["content-length"]  # as for a chunked LXD response
    return r


@respx.mock
async def test_download_streams_files_only(app):
    respx.route(url__startswith=F).mock(
        side_effect=by_path(
            {
                ("GET", "/x/data.bin"): httpx.Response(
                    200, content=b"\x00" * 70000, headers=head("file", 70000)
                ),
                ("GET", "/etc"): httpx.Response(
                    200, json={"metadata": []}, headers=head("directory")
                ),
                ("GET", "/nope"): lxd_err("not found", 404),
            }
        )
    )
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        r = await c.get(f"{U}/download", params={"path": "/x/data.bin"})
        assert r.status_code == 200 and len(r.content) == 70000
        assert r.headers["content-disposition"] == 'attachment; filename="data.bin"'
        assert r.headers["content-length"] == "70000"
        r = await c.get(f"{U}/download", params={"path": "/etc"})
        assert r.status_code == 400 and r.json()["detail"] == "not a regular file"
        assert (await c.get(f"{U}/download", params={"path": "/nope"})).status_code == 404


@respx.mock
async def test_download_without_length_and_odd_name(app):
    # respx can't serve two streamed bodies in one test, so this case stands alone
    respx.route(url__startswith=F).mock(side_effect=lambda req: no_length(b"1"))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        r = await c.get(f"{U}/download", params={"path": '/x/we"ird'})
    assert r.content == b"1"
    assert r.headers["content-disposition"] == 'attachment; filename="weird"'
    assert "content-length" not in r.headers  # LXD sent none: none is promised


@respx.mock
async def test_upload_mkdir_delete(app, services):
    up = respx.post(F, params={"path": "/root/a.txt"}).mock(return_value=lxd_ok(None))
    mk = respx.post(F, params={"path": "/root/d"}).mock(return_value=lxd_ok(None))
    bad = respx.post(F, params={"path": "/ro/x"}).mock(
        return_value=lxd_err("read-only file system", 500)
    )
    junk = respx.post(F, params={"path": "/junk"}).mock(return_value=httpx.Response(503, text="x"))
    rm = respx.delete(F, params={"path": "/root/d"}).mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "viewer", "viewerpw")}
        assert (
            await c.post(f"{U}/upload", params={"path": "/root/a.txt"}, content=b"x", headers=h)
        ).status_code == 403
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        r = await c.post(
            f"{U}/upload",
            params={"path": "/root/a.txt", "mode": "0600"},
            content=b"hello",
            headers=h,
        )
        assert r.status_code == 200
        req = up.calls.last.request
        assert req.content == b"hello" and req.headers["x-lxd-type"] == "file"
        assert req.headers["x-lxd-mode"] == "0600" and req.headers["x-lxd-write"] == "overwrite"
        assert dict(req.url.params) == {"project": "p", "path": "/root/a.txt"}
        assert (
            await c.post(
                f"{U}/upload", params={"path": "/root/a.txt", "mode": "999"}, content=b"", headers=h
            )
        ).status_code == 422
        await c.post(f"{U}/mkdir", params={"path": "/root/d"}, headers=h)
        assert mk.calls.last.request.headers["x-lxd-type"] == "directory"
        r = await c.post(f"{U}/mkdir", params={"path": "/ro/x"}, headers=h)
        assert r.status_code == 502 and "read-only" in r.json()["detail"] and bad.called
        r = await c.post(f"{U}/mkdir", params={"path": "/junk"}, headers=h)
        assert r.status_code == 502 and r.json()["detail"] == "LXD: HTTP 503" and junk.called
        assert (
            await c.delete(U, params={"path": "/root/d/"}, headers=h)
        ).status_code == 200 and rm.called
        assert (await c.delete(U, params={"path": "/"}, headers=h)).status_code == 400
    audit = [
        (e["action"], e["ok"], e["detail"]["path"]) for e in services.store.audit_entries()[:5]
    ]
    assert audit == [
        ("file.delete", True, "/root/d"),
        ("file.mkdir", False, "/junk"),
        ("file.mkdir", False, "/ro/x"),
        ("file.mkdir", True, "/root/d"),
        ("file.upload", True, "/root/a.txt"),
    ]


@respx.mock
async def test_open_stream_unreachable(lxd):
    respx.get(f"{EP1}/1.0/x").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(LXDUnavailable, match="ConnectError: down"):
        await lxd.open_stream("GET", "/1.0/x")
