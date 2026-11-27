"""Smaller review findings (D89): what project users see of other projects, upload caps."""

import pytest
import respx
from fastapi import HTTPException

from mcui.api.uploads import capped
from mcui.auth.policy import Scope
from mcui.config import Settings
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import EP1, fast_store, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair
from tests.test_api_instances import admin, app, services

__all__ = ["app", "services"]  # fixtures

USED = [
    "/1.0/instances/web1?project=web",
    "/1.0/instances/secret?project=other",
    "/1.0/profiles/default",
]


def test_keep_urls():
    assert Scope(None).keep_urls(USED) == USED
    assert Scope(frozenset({"web"})).keep_urls(USED) == ["/1.0/instances/web1?project=web"]


@pytest.fixture
def pat_app(tmp_path):
    store = fast_store()
    store.add_user("pat", "patpw", "project")
    store.set_grant("pat", "web", "viewer")
    settings = Settings(
        server={"data_dir": str(tmp_path), "secure_cookies": False, "max_upload_bytes": 10}
    )
    return create_app(
        services=build_services(settings, store=store, clients=lxd_pair()), static_dir=None
    )


@respx.mock
async def test_project_users_see_only_their_own_used_by(pat_app):
    respx.get(f"{EP1}/1.0/profiles/default").mock(
        return_value=lxd_ok({"name": "default", "used_by": USED})
    )
    respx.get(f"{EP1}/1.0/profiles").mock(
        return_value=lxd_ok([{"name": "default", "used_by": USED}])
    )
    respx.get(f"{EP1}/1.0/networks/n1").mock(return_value=lxd_ok({"name": "n1", "used_by": USED}))
    pools = [{"name": "remote", "driver": "ceph", "config": {"ceph.user.name": "admin"}}]
    respx.get(f"{EP1}/1.0/storage-pools").mock(return_value=lxd_ok(pools))
    async with client(pat_app) as c:
        await login(c, "pat", "patpw")
        for url in ("/api/profiles/web/default", "/api/networks/web/n1"):
            assert (await c.get(url)).json()["used_by"] == [USED[0]], url
        assert (await c.get("/api/profiles?project=web")).json()[0]["used_by"] == [USED[0]]
        assert (await c.get("/api/storage-pools")).json()[0]["config"] == {}


@respx.mock
async def test_global_roles_still_see_everything(app):
    respx.get(f"{EP1}/1.0/networks/n1").mock(return_value=lxd_ok({"name": "n1", "used_by": USED}))
    pools = [{"name": "remote", "driver": "ceph", "config": {"ceph.user.name": "admin"}}]
    respx.get(f"{EP1}/1.0/storage-pools").mock(return_value=lxd_ok(pools))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get("/api/networks/default/n1")).json()["used_by"] == USED
        assert (await c.get("/api/storage-pools")).json()[0]["config"] == pools[0]["config"]


@respx.mock(assert_all_called=False)
async def test_uploads_over_the_limit_are_refused(tmp_path, respx_mock):
    store = fast_store()
    store.add_user("admin", "adminpw", "admin")
    settings = Settings(
        server={"data_dir": str(tmp_path), "secure_cookies": False, "max_upload_bytes": 10}
    )
    a = create_app(
        services=build_services(settings, store=store, clients=lxd_pair()), static_dir=None
    )
    async with client(a) as c:
        h = await admin(c)
        r = await c.post(
            "/api/volumes/iso?project=default&pool=remote&name=big", content=b"x" * 11, headers=h
        )
        assert r.status_code == 413 and "limit of 10 bytes" in r.text
    assert not respx_mock.calls
    assert CSRF_HEADER  # the admin helper set it


class FakeRequest:
    def __init__(self, chunks, length=None, limit=10):
        self.chunks = chunks
        self.headers = {"content-length": str(length)} if length is not None else {}
        services = type("S", (), {"settings": Settings(server={"max_upload_bytes": limit})})
        self.app = type("A", (), {"state": type("St", (), {"services": services})})

    async def stream(self):
        for c in self.chunks:
            yield c


async def test_capped_counts_what_arrives():
    out = [c async for c in capped(FakeRequest([b"12345", b"12345"], length=10))]
    assert out == [b"12345", b"12345"]
    with pytest.raises(HTTPException) as e:  # a body bigger than it said
        _ = [c async for c in capped(FakeRequest([b"123456", b"123456"], length=10))]
    assert e.value.status_code == 413
    with pytest.raises(HTTPException) as e:  # no length: refused before LXD hears of it
        capped(FakeRequest([b"1"]))
    assert e.value.status_code == 411
    with pytest.raises(HTTPException):
        capped(FakeRequest([], length=11))
    assert [c async for c in capped(FakeRequest([b"x" * 100], limit=0))] == [b"x" * 100]


def test_attachment_names():
    from mcui.api.uploads import attachment

    assert attachment("café 1.txt") == (
        "attachment; filename=\"caf__1.txt\"; filename*=UTF-8''caf%C3%A9%201.txt"
    )
    assert attachment("a\nb") == "attachment; filename=\"a_b\"; filename*=UTF-8''a%0Ab"
    assert attachment("").startswith('attachment; filename="download"')


def test_renaming_onto_stale_grants(tmp_path):
    """Grants left on the new name don't clash or come back (D90)."""
    store = fast_store()
    store.add_user("pat", "patpw", "project")
    store.add_user("sam", "sampw", "project")
    store.set_grant("pat", "old", "admin")
    store.set_grant("pat", "new", "viewer")  # stale: "new" was deleted outside the app
    store.set_grant("sam", "new", "admin")  # stale too
    assert store.rename_project_grants("old", "new") == 1
    assert store.get_user("pat").grants == {"new": "admin"}
    assert store.get_user("sam").grants == {}


def test_renaming_grants_rolls_back(monkeypatch):
    store = fast_store()
    store.add_user("pat", "patpw", "project")
    store.set_grant("pat", "old", "admin")
    real = store.db.execute

    def failing(sql, *a):
        if sql.startswith("UPDATE grants"):
            raise RuntimeError("disk full")
        return real(sql, *a)

    store.db = type("DB", (), {"execute": staticmethod(failing)})()
    with pytest.raises(RuntimeError):
        store.rename_project_grants("old", "new")
    store.db = type("DB", (), {"execute": staticmethod(real)})()
    assert store.get_user("pat").grants == {"old": "admin"}


@respx.mock
async def test_acls_volumes_and_member_state_for_project_users(pat_app):
    respx.get(f"{EP1}/1.0/network-acls").mock(return_value=lxd_ok([{"name": "a", "used_by": USED}]))
    respx.get(f"{EP1}/1.0/network-acls/a").mock(return_value=lxd_ok({"name": "a", "used_by": USED}))
    respx.get(f"{EP1}/1.0/storage-pools/remote/volumes/custom/v1").mock(
        return_value=lxd_ok({"name": "v1", "used_by": USED})
    )
    respx.get(f"{EP1}/1.0/storage-pools/remote/volumes/custom/v1/state").mock(
        return_value=lxd_ok({})
    )
    respx.get(f"{EP1}/1.0/storage-pools/remote/volumes").mock(
        return_value=lxd_ok([{"name": "v1", "type": "custom", "used_by": USED}])
    )
    async with client(pat_app) as c:
        await login(c, "pat", "patpw")
        assert (await c.get("/api/network-acls?project=web")).json()[0]["used_by"] == [USED[0]]
        assert (await c.get("/api/network-acls/web/a")).json()["used_by"] == [USED[0]]
        assert (await c.get("/api/volumes/web/remote/v1")).json()["used_by"] == [USED[0]]
        vols = (await c.get("/api/volumes?project=web&pool=remote")).json()
        assert vols[0]["used_by"] == [USED[0]]
        r = await c.get("/api/networks/web/n1/state?target=node-1")
        assert r.status_code == 403 and "cluster role" in r.text


def test_pid1_fallback_without_the_private_attribute(monkeypatch):
    import signal

    import uvicorn

    from mcui import server as srv

    # a uvicorn release that renamed the attribute: its own handle_exit uses the new name
    monkeypatch.setattr(uvicorn.Server, "handle_exit", lambda self, sig, frame: None)
    s = srv.Server(uvicorn.Config("mcui.main:create_app", factory=True))
    del s._captured_signals
    s.should_exit = True
    monkeypatch.setattr(srv.os, "getpid", lambda: 1)
    s.handle_exit(signal.SIGTERM, None)  # no AttributeError
    assert s.should_exit
