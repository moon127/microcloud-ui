"""Every route declares exactly one access policy (D81, Phase 6). A route added without
one fails here, so nothing ships unchecked."""

import pytest
import respx
from fastapi.routing import APIRoute, APIWebSocketRoute

from mcui.auth.policy import Scope, allowed
from mcui.auth.store import User
from mcui.config import Settings
from mcui.lxd.client import LXDClient, LXDError
from mcui.lxd.projects import is_restricted
from mcui.main import create_app
from mcui.services import build_services
from tests.conftest import EP1, fast_store, lxd_err, lxd_ok
from tests.test_api import CSRF_HEADER, client, login, lxd_pair
from tests.test_api_ops import async_resp

# no session at all
PUBLIC = {("POST", "/api/auth/login"), ("GET", "/api/health")}
# only "signed in": about the user themselves, or what every page and the create wizard
# need and holds no project data (server features, pool names and free space, image
# servers and their catalogues)
SESSION_ONLY = {
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/server"),
    ("GET", "/api/storage-pools"),
    ("GET", "/api/images/remote/{remote}"),
    ("GET", "/api/image-registries"),
}


def _policies(dependant) -> set[str]:
    found = set()
    for d in dependant.dependencies:
        p = getattr(d.call, "POLICY", None)
        if p:
            found.add(p)
        found |= _policies(d)
    return found


def _flat(rs):
    """FastAPI 0.16x keeps included routers wrapped; their routes carry the full path (our
    routers set their own prefix) and every dependency (none are set per router)."""
    for r in rs:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from _flat(inner.routes)
        else:
            yield r


def routes(tmp_path):
    settings = Settings(server={"data_dir": str(tmp_path), "secure_cookies": False})
    app = create_app(
        services=build_services(settings, store=fast_store(), clients=lxd_pair()), static_dir=None
    )
    out = {}
    for r in _flat(app.routes):
        if isinstance(r, APIRoute):
            for m in r.methods - {"HEAD"}:
                out[(m, r.path)] = _policies(r.dependant)
        elif isinstance(r, APIWebSocketRoute):
            ep = getattr(r.endpoint, "POLICY", None)
            out[("WS", r.path)] = {ep} if ep else set()
    return out


def test_every_route_declares_one_policy(tmp_path):
    found = routes(tmp_path)
    assert len(found) >= 135
    problems = []
    for key, pols in sorted(found.items()):
        if key in PUBLIC:
            if pols:
                problems.append(f"{key}: public but has {sorted(pols)}")
            continue
        own = pols - {"session"}
        if key in SESSION_ONLY:
            if own or "session" not in pols:
                problems.append(f"{key}: should be session-only, has {sorted(pols)}")
        elif len(own) != 1:
            problems.append(f"{key}: {sorted(own) or 'no policy'}")
    assert problems == [], "\n".join(problems)


# --- enforcement for a project user (D81) ---------------------------------------------


INST = f"{EP1}/1.0/instances"


def test_roles_and_grants():
    admin, viewer = User(1, "a", "admin"), User(2, "v", "viewer")
    pat = User(3, "p", "project", {"web": "operator", "lab": "viewer"})
    assert allowed(admin, "any", "manage") and allowed(viewer, "any", "read")
    assert not allowed(viewer, "any", "operate")
    assert allowed(pat, "web", "operate") and not allowed(pat, "web", "manage")
    assert allowed(pat, "lab", "read") and not allowed(pat, "lab", "operate")
    assert not allowed(pat, "other", "read")
    assert Scope.of(viewer).sees("x") and Scope.of(pat).projects == {"web", "lab"}
    assert not Scope.of(pat).sees("other")


@pytest.fixture
def scoped(tmp_path):
    """The app with pat: operator in the restricted project web, viewer in lab."""
    from mcui.config import Settings
    from mcui.main import create_app
    from mcui.services import build_services
    from tests.conftest import fast_store
    from tests.test_api import lxd_pair

    store = fast_store()
    store.add_user("pat", "patpw", "project")
    store.set_grant("pat", "web", "operator")
    store.set_grant("pat", "lab", "viewer")
    store.set_grant("pat", "old", "operator")  # a project since deleted in LXD
    settings = Settings(server={"data_dir": str(tmp_path), "secure_cookies": False})
    app = create_app(
        services=build_services(settings, store=store, clients=lxd_pair()), static_dir=None
    )
    return app


@respx.mock
async def test_project_user_rights(scoped):
    respx.get(f"{INST}/c1", params={"project": "web", "recursion": "1"}).mock(
        return_value=lxd_ok({"name": "c1"})
    )
    project = respx.get(f"{EP1}/1.0/projects/web").mock(
        return_value=lxd_ok({"config": {"restricted": "true"}})
    )
    state = respx.put(f"{INST}/c1/state").mock(return_value=async_resp())
    async with client(scoped) as c:
        h = {CSRF_HEADER: await login(c, "pat", "patpw")}
        assert (await c.get("/api/instances/web/c1")).json() == {"name": "c1"}
        # another project: not found, not "forbidden", so its existence isn't revealed
        r = await c.get("/api/instances/other/c1")
        assert r.status_code == 404 and r.json() == {"detail": "not found"}
        # a project they can see, but without the right
        r = await c.put("/api/instances/lab/c1/state", json={"action": "start"}, headers=h)
        assert r.status_code == 403 and "operate right in lab" in r.text
        r = await c.put("/api/instances/web/c1/state", json={"action": "start"}, headers=h)
        assert r.status_code == 200 and state.called
        await c.put("/api/instances/web/c1/state", json={"action": "stop"}, headers=h)
        assert project.call_count == 1  # the restricted check is cached
        r = await c.delete("/api/instances/web/c1", headers=h)
        assert r.status_code == 403 and "manage right in web" in r.text
        # creating: the project is in the body
        new = {"name": "c2", "image": {"alias": "a"}, "project": "other"}
        assert (await c.post("/api/instances", json=new, headers=h)).status_code == 404
        r = await c.post("/api/instances", json={**new, "project": "web"}, headers=h)
        assert r.status_code == 403
        # cluster-wide pages need a cluster role
        assert (await c.get("/api/ceph")).status_code == 403


@respx.mock
async def test_unrestricted_project_turns_read_only(scoped):
    respx.get(f"{EP1}/1.0/projects/web").mock(return_value=lxd_ok({"config": {}}))
    respx.get(f"{EP1}/1.0/projects/old").mock(return_value=lxd_err("Project not found", 404))
    async with client(scoped) as c:
        h = {CSRF_HEADER: await login(c, "pat", "patpw")}
        r = await c.put("/api/instances/web/c1/state", json={"action": "start"}, headers=h)
        assert r.status_code == 403 and "no longer restricted" in r.text
        r = await c.put("/api/instances/old/c1/state", json={"action": "start"}, headers=h)
        assert r.status_code == 403  # a vanished project grants nothing beyond read


@respx.mock
async def test_is_restricted_passes_other_errors_on():
    import ssl

    respx.get(f"{EP1}/1.0/projects/web").mock(return_value=lxd_err("boom", 500))
    lxd = LXDClient([EP1], verify=ssl.create_default_context())
    with pytest.raises(LXDError):
        await is_restricted(lxd, "web")
    await lxd.aclose()


# --- what a project user sees (Phase 6b) ----------------------------------------------

INSTANCES = [
    {"name": n, "project": p, "status": "Running", "location": "m1"}
    for n, p in (("a", "web"), ("b", "lab"), ("x", "other"))
]


@respx.mock
async def test_lists_are_filtered(scoped):
    respx.get(f"{EP1}/1.0/instances", params={"recursion": "2", "all-projects": "true"}).mock(
        return_value=lxd_ok(INSTANCES)
    )
    respx.get(f"{EP1}/1.0/profiles", params={"recursion": "1", "project": "web"}).mock(
        return_value=lxd_ok([{"name": "default", "description": "", "config": {}, "devices": {}}])
    )
    respx.get(f"{EP1}/1.0/projects", params={"recursion": "1"}).mock(
        return_value=lxd_ok([{"name": p} for p in ("default", "web", "lab", "other")])
    )
    respx.get(f"{EP1}/1.0/network-allocations", params={"all-projects": "true"}).mock(
        return_value=lxd_ok(
            [{"address": "10.0.0.2", "project": "web"}, {"address": "10.0.0.9", "project": "other"}]
        )
    )
    respx.get(f"{EP1}/1.0/networks/shared/leases", params={"project": "web"}).mock(
        return_value=lxd_ok(
            [{"hostname": "a", "project": "web"}, {"hostname": "x", "project": "other"}]
        )
    )
    async with client(scoped) as c:
        await login(c, "pat", "patpw")
        assert {i["name"] for i in (await c.get("/api/instances")).json()} == {"a", "b"}
        assert (await c.get("/api/profiles", params={"project": "web"})).status_code == 200
        for path in (
            "/api/profiles",
            "/api/images",
            "/api/networks",
            "/api/volumes",
            "/api/network-acls",
            "/api/placement-groups",
        ):
            r = await c.get(path, params={"project": "other"})
            assert r.status_code == 404, path
        r = await c.get("/api/networks", params={"project": "web", "target": "m1"})
        assert r.status_code == 403  # the host's own interfaces
        assert [p["name"] for p in (await c.get("/api/projects")).json()] == ["web", "lab"]
        allocs = (await c.get("/api/network-allocations")).json()
        assert [a["project"] for a in allocs] == ["web"]
        assert (
            await c.get("/api/network-allocations", params={"project": "other"})
        ).status_code == 404
        leases = (await c.get("/api/networks/web/shared/leases")).json()
        assert [x["hostname"] for x in leases] == ["a"]


@respx.mock
async def test_operations_are_filtered(scoped):
    ops = {
        "running": [
            {"id": "w", "project": "web", "created_at": "2"},
            {"id": "o", "project": "other", "created_at": "1"},
            {"id": "c", "project": "", "created_at": "0"},  # cluster-wide (an evacuation)
        ]
    }
    respx.get(f"{EP1}/1.0/operations", params={"recursion": "2", "all-projects": "true"}).mock(
        return_value=lxd_ok(ops)
    )
    respx.get(f"{EP1}/1.0/operations/w").mock(return_value=lxd_ok(ops["running"][0]))
    respx.get(f"{EP1}/1.0/operations/o").mock(return_value=lxd_ok(ops["running"][1]))
    async with client(scoped) as c:
        await login(c, "pat", "patpw")
        assert [o["id"] for o in (await c.get("/api/operations")).json()] == ["w"]
        assert (await c.get("/api/operations/w")).json()["id"] == "w"
        assert (await c.get("/api/operations/o")).status_code == 404
        assert (await c.get("/api/operations/o", params={"wait": 5})).status_code == 404


async def test_metrics_are_filtered(scoped, monkeypatch):
    import mcui.api.metrics as metrics_api

    services = scoped.state.services

    async def fake_top(store, metric, s, e, limit, match):
        rows = [
            {"project": p, "name": n, "value": v}
            for p, n, v in (("other", "x", 9), ("web", "a", 5), ("lab", "b", 1))
        ]
        return {
            "metric": metric,
            "rows": [r for r in rows if match.get("project") in (None, r["project"])][:limit],
        }

    async def fake_find(name, match):
        return [(1, {"project": p, "name": n}, None) for p, n in (("web", "a"), ("other", "x"))] + [
            (3, {"member": "m1"}, None)
        ]

    async def fake_snapshot():
        return {
            "members": [{"name": "m1"}],
            "instances": [{"project": "web", "name": "a"}, {"project": "other", "name": "x"}],
        }

    monkeypatch.setattr(metrics_api, "top", fake_top)
    monkeypatch.setattr(services.metrics, "find_series", fake_find)
    monkeypatch.setattr(services.live, "snapshot", fake_snapshot)

    from types import SimpleNamespace

    async def fake_query(name, match, s, e, fn="value", res=None, by=None):
        series = [
            SimpleNamespace(labels={"project": p, "name": n, "device": "eth0"}, points=[(10, 1.0)])
            for p, n in (("web", "a"), ("other", "x"))
        ]
        return SimpleNamespace(series=series, resolution=15, as_dict=lambda: {"series": []})

    async def fake_nics():
        return {("web", "a", "eth0"): "net-web", ("other", "x", "eth0"): "net-other"}

    monkeypatch.setattr(services.metrics, "query", fake_query)
    monkeypatch.setattr(services.instances, "nic_networks", fake_nics)
    async with client(scoped) as c:
        await login(c, "pat", "patpw")
        top = (await c.get("/api/metrics/top", params={"metric": "cpu", "limit": 5})).json()
        assert [r["name"] for r in top["rows"]] == ["a", "b"]
        r = await c.get("/api/metrics/top", params={"metric": "cpu", "project": "other"})
        assert r.status_code == 404
        series = (await c.get("/api/metrics/series", params={"name": "m"})).json()
        assert series == [{"project": "web", "name": "a"}]  # no other project, no host series
        r = await c.get("/api/metrics/query", params={"name": "m"})
        assert r.status_code == 403 and "must match a project" in r.text
        r = await c.get("/api/metrics/query", params={"name": "m", "match": "project=other"})
        assert r.status_code == 404
        r = await c.get("/api/metrics/query", params={"name": "m", "match": "project=web"})
        assert r.status_code == 200
        live = (await c.get("/api/metrics/live")).json()
        assert live["members"] == [] and [i["name"] for i in live["instances"]] == ["a"]
        top = (await c.get("/api/metrics/top", params={"metric": "cpu", "project": "web"})).json()
        assert [r["name"] for r in top["rows"]] == ["a"]
        traffic = (await c.get("/api/metrics/network-traffic")).json()
        assert [x["labels"] for x in traffic["rx"]] == [{"network": "net-web"}]


async def test_audit_is_filtered(scoped):
    store = scoped.state.services.store
    store.audit("admin", "instance.start", "web/a")
    store.audit("admin", "instance.start", "other/x")
    store.audit("admin", "user.grant", "pat", detail={"project": "web"})
    store.audit("pat", "auth.login")
    async with client(scoped) as c:
        await login(c, "pat", "patpw")
        assert [e["target"] for e in (await c.get("/api/audit")).json()] == ["web/a"]


def test_events_keep_the_users_projects():
    from mcui.api.wsproxy import ProjectEvents

    keep = ProjectEvents(Scope(frozenset({"web"})), lambda m: True)
    assert keep('{"type":"lifecycle","project":"web"}')
    assert not keep('{"type":"lifecycle","project":"other"}')
    assert not keep('{"type":"operation"}')  # no project: cluster-wide
    assert not keep(b"\x01") and not keep("[1]")
