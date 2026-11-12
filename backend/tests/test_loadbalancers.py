"""OVN load balancers and pools API (Phase 5b); payloads as checked live on LXD 6.10."""

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
NET = f"{EP1}/1.0/networks/default"


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


def recorded(name):
    return json.loads((RECORDED / name).read_text())["metadata"]


@respx.mock
async def test_reads(app):
    respx.get(f"{NET}/load-balancers").mock(
        return_value=lxd_ok(
            [
                {
                    "listen_address": "198.51.100.12",
                    "description": "mcui test LB",
                    "config": {},
                    "backends": [],
                    "ports": [{"protocol": "tcp", "listen_port": "80", "target_pool": "web"}],
                }
            ]
        )
    )
    respx.get(f"{NET}/load-balancer-pools").mock(
        return_value=lxd_ok(recorded("1.0_networks_default_load-balancer-pools_recursion_1.json"))
    )
    respx.get(f"{NET}/load-balancer-pools/web/state").mock(
        return_value=lxd_ok({"targets": [{"name": "c1", "status": "offline"}]})
    )
    async with client(app) as c:
        await login(c, "viewer", "viewerpw")
        lbs = (await c.get("/api/networks/default/default/load-balancers")).json()
        assert lbs[0]["ports"][0]["target_pool"] == "web"
        pools = (await c.get("/api/networks/default/default/load-balancer-pools")).json()
        assert pools[0]["config"]["healthcheck"] == "true"  # as recorded
        state = (await c.get("/api/networks/default/default/load-balancer-pools/web/state")).json()
        assert state["targets"][0]["status"] == "offline"


@respx.mock
async def test_load_balancer_changes(app, services):
    route = respx.route(url__startswith=f"{NET}/load-balancers").mock(return_value=async_resp())
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        # 0.0.0.0: LXD allocates from the uplink; a port to a pool
        await c.post(
            "/api/networks/default/default/load-balancers",
            json={
                "listen_address": "0.0.0.0",  # noqa: S104 - allocation, not a bind
                "ports": [{"protocol": "tcp", "listen_port": "443", "target_pool": "api"}],
            },
            headers=h,
        )
        sent = json.loads(route.calls.last.request.content)
        assert sent == {
            "listen_address": "0.0.0.0",  # noqa: S104
            "description": "",
            "config": {},
            "backends": [],
            "ports": [
                {"protocol": "tcp", "listen_port": "443", "description": "", "target_pool": "api"}
            ],
        }
        # named backends instead: LXD wants exactly one kind of target per port
        await c.put(
            "/api/networks/default/default/load-balancers/198.51.100.54",
            json={
                "backends": [{"name": "b1", "target_address": "192.0.2.4", "target_port": "80"}],
                "ports": [{"protocol": "tcp", "listen_port": "80", "target_backend": ["b1"]}],
            },
            headers=h,
        )
        sent = json.loads(route.calls.last.request.content)
        assert sent["ports"] == [
            {"protocol": "tcp", "listen_port": "80", "description": "", "target_backend": ["b1"]}
        ]
        assert sent["backends"][0]["target_address"] == "192.0.2.4"
        await c.delete("/api/networks/default/default/load-balancers/198.51.100.54", headers=h)
        assert route.calls.last.request.method == "DELETE"
        for bad in (
            {"listen_address": "nope"},
            {"listen_address": "198.51.100.9", "backends": [{"name": "b", "target_address": "x"}]},
            {
                "listen_address": "198.51.100.9",
                "ports": [{"protocol": "sctp", "listen_port": "80", "target_pool": "p"}],
            },
        ):
            r = await c.post("/api/networks/default/default/load-balancers", json=bad, headers=h)
            assert r.status_code == 422, bad
        r = await c.delete("/api/networks/default/default/load-balancers/not-an-ip", headers=h)
        assert r.status_code == 422
    assert [a[:2] for a in actions(services)[:3]] == [
        ("network-load-balancer.delete", "default/default/198.51.100.54"),
        ("network-load-balancer.update", "default/default/198.51.100.54"),
        ("network-load-balancer.create", "default/default/0.0.0.0"),
    ]


@respx.mock
async def test_pool_changes(app, services):
    route = respx.route(url__startswith=f"{NET}/load-balancer-pools").mock(
        return_value=async_resp()
    )
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        await c.post(
            "/api/networks/default/default/load-balancer-pools",
            json={
                "name": "api",
                "target_port": "8080",
                "instances": ["c3"],
                "healthcheck": True,
                "healthcheck_interval": 10,
                "description": "api tier",
            },
            headers=h,
        )
        assert json.loads(route.calls.last.request.content) == {
            "name": "api",
            "description": "api tier",
            "config": {
                "protocol": "tcp",
                "target_port": "8080",
                "healthcheck": "true",
                "healthcheck.interval": "10",
            },
            "instances": [{"name": "c3"}],
        }
        # without a health check its keys are left out (LXD then drops them)
        await c.put(
            "/api/networks/default/default/load-balancer-pools/api",
            json={"target_port": "8080", "instances": ["c2", "c3"]},
            headers=h,
        )
        sent = json.loads(route.calls.last.request.content)
        assert sent["config"] == {"protocol": "tcp", "target_port": "8080"}
        assert sent["instances"] == [{"name": "c2"}, {"name": "c3"}]
        await c.delete("/api/networks/default/default/load-balancer-pools/api", headers=h)
        r = await c.post(
            "/api/networks/default/default/load-balancer-pools",
            json={"name": "x", "target_port": "80", "healthcheck": True, "healthcheck_interval": 0},
            headers=h,
        )
        assert r.status_code == 422
    assert [a[0] for a in actions(services)[:3]] == [
        "network-load-balancer-pool.delete",
        "network-load-balancer-pool.update",
        "network-load-balancer-pool.create",
    ]


@respx.mock
async def test_refusals_and_roles(app, services):
    respx.delete(f"{NET}/load-balancer-pools/web").mock(
        return_value=lxd_err(
            'Failed deleting load balancer pool: Pool "web" is still referenced by at least '
            "one load balancer port",
            400,
        )
    )
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "viewer", "viewerpw")}
        r = await c.post(
            "/api/networks/default/default/load-balancer-pools",
            json={"name": "x", "target_port": "80"},
            headers=h,
        )
        assert r.status_code == 403
    async with client(app) as c:
        h = {CSRF_HEADER: await login(c, "admin", "adminpw")}
        r = await c.delete("/api/networks/default/default/load-balancer-pools/web", headers=h)
        assert r.status_code == 502 and "still referenced" in r.text
    assert actions(services)[0][:3] == (
        "network-load-balancer-pool.delete",
        "default/default/web",
        False,
    )
