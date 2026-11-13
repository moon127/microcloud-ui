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
    """The app with project users switched on (they're locked out until 6b), and pat:
    operator in the restricted project web, viewer in lab."""
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
    app.state.project_access = True
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
