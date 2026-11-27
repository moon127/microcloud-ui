"""Request values can't steer the app's LXD requests to another endpoint (0.3.4, D88).

Found in the 2026-11-27 review: httpx removes ``..`` segments, so an image alias of
``x/../../../certificates/<fp>`` turned a project admin's alias delete into
``DELETE /1.0/certificates/<fp>`` as the app's admin certificate, and ``?pool=../projects#``
listed every project. The LXD client now refuses such paths outright."""

import ssl

import pytest
import respx

from mcui.lxd.client import LXDClient, UnsafePath, check_path
from tests.conftest import EP1, lxd_ok
from tests.test_api import client, login
from tests.test_api_instances import admin, app, services

__all__ = ["app", "services"]  # fixtures


@pytest.mark.parametrize(
    "path",
    [
        "/1.0/images/aliases/x/../../certificates/abc",
        "/1.0/images/aliases/./x",
        "/1.0/storage-pools/../projects",
        "/1.0/projects#/volumes",
        "/1.0/projects?recursion=1",
        "/1.0/instances//x",
        "/1.0/instances/%2E%2E/x",
        "/1.0/instances/x\\\\..",
        "/1.0/instances/x\\n",
        "relative/path",
        "/1.0/instances/x/",
    ],
)
def test_unsafe_paths_are_refused(path):
    with pytest.raises(UnsafePath):
        check_path(path)


def test_ordinary_paths_pass():
    for path in (
        "/1.0",
        "/1.0/images/aliases/ubuntu/24.04",
        "/1.0/instances/c1/logs/exec-output/exec_1.stdout",
        "/1.0/operations/4a4b-uuid/websocket",
    ):
        assert check_path(path) == path


async def test_the_client_refuses_before_sending():
    c = LXDClient([EP1], verify=ssl.create_default_context())
    with respx.mock(assert_all_called=False) as mock:
        for call in (
            c.raw("DELETE", "/1.0/images/aliases/x/../../../certificates/abc"),
            c.open_stream("GET", "/1.0/../certificates"),
            c.websocket("/1.0/operations/../events"),
        ):
            with pytest.raises(UnsafePath):
                await call
        assert not mock.calls
    await c.aclose()


@respx.mock(assert_all_called=False)
async def test_alias_traversal_is_refused(app, respx_mock):
    respx_mock.get(f"{EP1}/1.0/projects/p").mock(
        return_value=lxd_ok({"config": {"restricted": "true"}})
    )
    ok = respx_mock.delete(f"{EP1}/1.0/images/aliases/ubuntu/24.04").mock(return_value=lxd_ok(None))
    async with client(app) as c:
        h = await admin(c)
        for evil in (
            "x/%2E%2E/%2E%2E/%2E%2E/certificates/abcd",
            "x/%2E/y",
            "x//y",
        ):
            r = await c.delete(f"/api/images/p/aliases/{evil}", headers=h)
            assert r.status_code == 422, evil
        assert (await c.delete("/api/images/p/aliases/ubuntu/24.04", headers=h)).status_code == 200
    assert [
        str(call.request.url.path) for call in respx_mock.calls if call.request.method == "DELETE"
    ] == ["/1.0/images/aliases/ubuntu/24.04"]
    assert ok.called


@respx.mock(assert_all_called=False)
async def test_pool_and_target_values_are_names(app, respx_mock):
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        for q in ("pool=../projects%23", "pool=a/b"):
            r = await c.get(f"/api/volumes?project=default&all=true&{q}")
            assert r.status_code == 422, q
        r = await c.get("/api/networks?project=default&target=../certificates%23")
        assert r.status_code == 422
        h = await admin(c)
        r = await c.post("/api/instances/default/c1/move", json={"pool": "../x"}, headers=h)
        assert r.status_code == 422
        r = await c.post(
            "/api/instances/default/c1/copy", json={"name": "c2", "target": "a/b"}, headers=h
        )
        assert r.status_code == 422
    assert not respx_mock.calls


@respx.mock(assert_all_called=False)
async def test_an_unsafe_path_from_elsewhere_is_a_400(app, respx_mock):
    """Operation ids aren't name-checked; ``..`` now stops at the client."""
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        r = await c.get("/api/operations/%2E%2E")
        assert r.status_code == 400 and "unsafe LXD path" in r.text
    assert not respx_mock.calls
