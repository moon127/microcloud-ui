"""Placement groups API (LXD 6.6+, Phase 5b)."""

import json
from pathlib import Path

import pytest
import respx

from mcui.config import Settings
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import EP1, fast_store, lxd_err, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair
from tests.test_api_ops import actions, async_resp

RECORDED = Path(__file__).parent / "fixtures/recorded-6.10"


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
async def test_list_and_get(app):
    rec = json.loads(
        (RECORDED / "1.0_placement-groups_recursion_1_all-projects_true.json").read_text()
    )
    lst = respx.get(f"{EP1}/1.0/placement-groups").mock(return_value=lxd_ok(rec["metadata"]))
    one = respx.get(f"{EP1}/1.0/placement-groups/pg-spread").mock(
        return_value=lxd_ok(rec["metadata"][0])
    )
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        r = await c.get("/api/placement-groups", params={"project": "lab"})
        assert r.json()[0]["config"] == {"policy": "spread", "rigor": "permissive"}
        assert lst.calls.last.request.url.params["project"] == "lab"
        r = await c.get("/api/placement-groups/default/pg-spread")
        assert r.json()["used_by"] == ["/1.0/instances/c2", "/1.0/instances/c3"]
        assert one.called


@respx.mock
async def test_changes_are_admin_audited_operations(app, services):
    route = respx.route(url__startswith=f"{EP1}/1.0/placement-groups").mock(
        return_value=async_resp()
    )
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        r = await c.post(
            "/api/placement-groups",
            json={"name": "web", "project": "lab", "policy": "spread", "description": "d"},
            headers=h,
        )
        assert r.json()["operation"]["id"] == "op1"
        req = route.calls.last.request
        assert req.url.params["project"] == "lab"
        assert json.loads(req.content) == {
            "name": "web",
            "description": "d",
            "config": {"policy": "spread", "rigor": "strict"},  # strict unless asked
        }
        await c.put(
            "/api/placement-groups/lab/web",
            json={"policy": "compact", "rigor": "permissive"},
            headers=h,
        )
        assert json.loads(route.calls.last.request.content)["config"] == {
            "policy": "compact",
            "rigor": "permissive",
        }
        await c.post("/api/placement-groups/lab/web/rename", json={"name": "web2"}, headers=h)
        await c.delete("/api/placement-groups/lab/web2", headers=h)
        assert route.calls.last.request.method == "DELETE"
        # LXD's own values only
        r = await c.post(
            "/api/placement-groups", json={"name": "x", "policy": "anywhere"}, headers=h
        )
        assert r.status_code == 422
    assert [a[:2] for a in actions(services)[:4]] == [
        ("placement-group.delete", "lab/web2"),
        ("placement-group.rename", "lab/web"),
        ("placement-group.update", "lab/web"),
        ("placement-group.create", "lab/web"),
    ]


@respx.mock
async def test_viewer_cannot_change_and_lxd_refusals_pass_through(app, services):
    respx.delete(f"{EP1}/1.0/placement-groups/used").mock(
        return_value=lxd_err('Placement group "used" is currently in use', 400)
    )
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "viewer", "viewerpw")}
        r = await c.post("/api/placement-groups", json={"name": "x", "policy": "spread"}, headers=h)
        assert r.status_code == 403
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        r = await c.delete("/api/placement-groups/default/used", headers=h)
        assert r.status_code == 502 and "currently in use" in r.text
    assert actions(services)[0][:3] == ("placement-group.delete", "default/used", False)
