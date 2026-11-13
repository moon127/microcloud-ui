"""OVN network peers (Phase 10a, D84). Shapes as seen live on LXD 6.10."""

import json

import respx

from mcui.config import Settings
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import EP1, fast_store, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair
from tests.test_api_instances import admin, app, async_resp, last_audit, services

__all__ = ["app", "services"]  # fixtures

PEERS = f"{EP1}/1.0/networks/ovn1/peers"
U = "/api/networks/default/ovn1/peers"
PEER = {
    "name": "to2",
    "description": "test",
    "target_project": "default",
    "target_network": "ovn2",
    "status": "Pending",
    "config": {},
    "used_by": [],
}


@respx.mock
async def test_list(app):
    route = respx.get(PEERS).mock(return_value=lxd_ok([PEER]))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get(U)).json() == [PEER]
    assert dict(route.calls.last.request.url.params) == {"recursion": "1", "project": "default"}
    respx.get(PEERS).mock(return_value=lxd_ok(None))
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        assert (await c.get(U)).json() == []


@respx.mock
async def test_create_update_delete(app, services):
    create = respx.post(PEERS).mock(return_value=async_resp())
    put = respx.put(f"{PEERS}/to2").mock(return_value=lxd_ok(None))
    delete = respx.delete(f"{PEERS}/to2").mock(return_value=lxd_ok(None))
    new = {"name": "to2", "target_project": "lab", "target_network": "ovn2"}
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "viewer", "viewerpw")}
        assert (await c.post(U, json=new, headers=h)).status_code == 403
        h = await admin(c)
        assert (await c.post(U, json=new, headers=h)).status_code == 200
        assert json.loads(create.calls.last.request.content) == {
            **new,
            "description": "",
            "config": {},
        }
        assert last_audit(services)[0] == "network-peer.create"
        assert last_audit(services)[4] == {"to": "lab/ovn2"}
        await c.put(f"{U}/to2", json={"description": "d"}, headers=h)
        assert json.loads(put.calls.last.request.content) == {"description": "d", "config": {}}
        await c.delete(f"{U}/to2", headers=h)
        assert delete.called and last_audit(services)[0] == "network-peer.delete"
        bad = {**new, "target_network": "../x"}
        assert (await c.post(U, json=bad, headers=h)).status_code == 422


@respx.mock
async def test_a_project_admin_peers_only_to_projects_they_see(tmp_path):
    store = fast_store()
    store.add_user("pat", "patpw", "project")
    store.set_grant("pat", "web", "admin")
    store.set_grant("pat", "lab", "viewer")
    settings = Settings(server={"data_dir": str(tmp_path), "secure_cookies": False})
    app = create_app(
        services=build_services(settings, store=store, clients=lxd_pair()), static_dir=None
    )
    respx.get(f"{EP1}/1.0/projects/web").mock(
        return_value=lxd_ok({"config": {"restricted": "true"}})
    )
    create = respx.post(PEERS).mock(return_value=async_resp())
    url = "/api/networks/web/ovn1/peers"
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "pat", "patpw")}
        new = {"name": "p", "target_project": "other", "target_network": "n"}
        assert (await c.post(url, json=new, headers=h)).status_code == 404
        assert not create.called
        r = await c.post(url, json={**new, "target_project": "lab"}, headers=h)
        assert r.status_code == 200 and create.called
        r = await c.post(
            "/api/networks/lab/ovn1/peers", json={**new, "target_project": "web"}, headers=h
        )
        assert r.status_code == 403  # only a viewer in lab
