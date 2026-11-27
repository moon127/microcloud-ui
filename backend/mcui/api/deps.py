"""Request dependencies: services, session cookie, CSRF, and who may do what (D81).

Every API route declares exactly one access policy through one of the dependencies here
marked with ``POLICY`` (a test walks every route to make sure). Project-scoped checks
answer 404 for projects the user can't see, so they don't learn what exists."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, TypeVar

from fastapi import Depends, HTTPException, Request
from starlette.requests import HTTPConnection

from mcui.auth.policy import (
    Action,
    Scope,
    allowed,
    host_device_changes,
    may_use_host_devices,
)
from mcui.auth.store import Session, Store
from mcui.hostfs import exposes_hidden
from mcui.lxd.client import LXDClient
from mcui.metrics.live import LiveMetrics
from mcui.services import Services

COOKIE = "mcui_session"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


async def get_services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


ServicesDep = Annotated[Services, Depends(get_services)]


async def get_store(services: ServicesDep) -> Store:
    return services.store


StoreDep = Annotated[Store, Depends(get_store)]


async def current_session(request: Request, store: StoreDep) -> Session:
    token = request.cookies.get(COOKIE)
    session = store.get_session(token) if token else None
    if session is None:
        raise HTTPException(status_code=401, detail="not logged in")
    if request.method not in SAFE_METHODS and not store.csrf_ok(
        session, request.headers.get(CSRF_HEADER)
    ):
        raise HTTPException(status_code=403, detail="missing or invalid CSRF token")
    return session


F = TypeVar("F", bound=Callable[..., Any])


def _policy(name: str) -> Callable[[F], F]:
    """Label a dependency with the access policy it enforces (the every-route test reads it)."""

    def mark(fn: F) -> F:
        fn.POLICY = name  # type: ignore[attr-defined]
        return fn

    return mark


_policy("session")(current_session)  # any signed-in user: who am I, log out
SessionDep = Annotated[Session, Depends(current_session)]


@_policy("cluster-admin")
async def require_admin(session: SessionDep) -> Session:
    if not session.user.is_admin:
        raise HTTPException(status_code=403, detail="admin role required")
    return session


AdminDep = Annotated[Session, Depends(require_admin)]


async def get_lxd(services: ServicesDep) -> LXDClient:
    if services.lxd is None:
        raise HTTPException(status_code=503, detail="LXD is not configured")
    return services.lxd


LXDDep = Annotated[LXDClient, Depends(get_lxd)]


async def get_live(services: ServicesDep) -> LiveMetrics:
    if services.live is None:
        raise HTTPException(status_code=503, detail="LXD is not configured")
    return services.live


LiveDep = Annotated[LiveMetrics, Depends(get_live)]


# --- access policies (D81) ----------------------------------------------------------------


@_policy("cluster-read")
async def require_cluster_read(session: SessionDep) -> Session:
    """A global role: cluster-wide views (members, Ceph, OVN, warnings, storage pools...)."""
    if session.user.role == "project":
        raise HTTPException(status_code=403, detail="needs a cluster role")
    return session


ClusterReadDep = Annotated[Session, Depends(require_cluster_read)]


@_policy("scoped")
async def get_scope(session: SessionDep) -> Scope:
    """For list routes: the projects this user may see, to filter what's returned."""
    return Scope.of(session.user)


ScopeDep = Annotated[Scope, Depends(get_scope)]

RESTRICTED_TTL = 30.0  # how long a project's config (restricted, features) is trusted

# Writes a project user makes here land on the project's own objects only if the project
# has that feature. Without it LXD resolves the project to "default" and checks
# restrictions there, where there are none: a restricted project's admin could edit
# default's profiles (LXD's AllowProfileUpdate runs against the effective project; found
# in the 2026-11-27 review, D88). LXD refused network writes in the same case (seen live);
# they're covered all the same.
FEATURE_BY_PREFIX: tuple[tuple[str, str, str], ...] = (
    ("/api/profiles", "features.profiles", "profiles"),
    ("/api/images", "features.images", "images"),
    ("/api/volumes", "features.storage.volumes", "storage volumes"),
    ("/api/networks", "features.networks", "networks"),
    ("/api/network-acls", "features.networks", "networks"),
)


async def _config(request: HTTPConnection, lxd: LXDClient, project: str) -> dict[str, str]:
    from mcui.lxd.projects import NoSuchProject, project_config

    cache: dict[str, tuple[float, dict[str, str]]] = request.app.state.restricted_cache
    hit = cache.get(project)
    if hit and time.monotonic() - hit[0] < RESTRICTED_TTL:
        return hit[1]
    try:
        value = await project_config(lxd, project)
    except NoSuchProject:
        value = {}
    cache[project] = (time.monotonic(), value)
    return value


async def _restricted(request: HTTPConnection, lxd: LXDClient, project: str) -> bool:
    return (await _config(request, lxd, project)).get("restricted") == "true"


project_restricted = _restricted  # for websockets, which have no dependencies


async def _authorize(
    request: Request, session: Session, lxd: LXDClient | None, project: str, action: Action
) -> None:
    user = session.user
    if not allowed(user, project, action):
        if Scope.of(user).sees(project):  # they know it exists: say why
            raise HTTPException(status_code=403, detail=f"needs the {action} right in {project}")
        raise HTTPException(status_code=404, detail="not found")
    # a project user's operate/manage rights need LXD to guard the host (D81)
    needs_restricted = user.role == "project" and action != "read"
    if not needs_restricted or lxd is None:
        return
    if not await _restricted(request, lxd, project):
        raise HTTPException(
            status_code=403,
            detail=f"project {project} is no longer restricted, so it's read-only for now",
        )
    path = request.url.path
    for prefix, feature, kind in FEATURE_BY_PREFIX:
        if path == prefix or path.startswith(prefix + "/"):
            if (await _config(request, lxd, project)).get(feature) != "true":
                raise HTTPException(
                    status_code=403,
                    detail=f"project {project} shares the default project's {kind} ({feature}"
                    " is off), so its users can't change them",
                )
            break


def _project_dep(action: Action) -> Callable[..., Awaitable[Session]]:
    @_policy(f"project-{action}")
    async def dep(request: Request, session: SessionDep, services: ServicesDep) -> Session:
        project = request.path_params.get("project")
        if not isinstance(project, str):  # pragma: no cover - a route wiring error
            raise RuntimeError(f"{request.url.path}: project-{action} needs a {{project}}")
        await _authorize(request, session, services.lxd, project, action)
        return session

    return dep


ProjectReadDep = Annotated[Session, Depends(_project_dep("read"))]
ProjectOperateDep = Annotated[Session, Depends(_project_dep("operate"))]
ProjectManageDep = Annotated[Session, Depends(_project_dep("manage"))]


@_policy("project-host-devices")
async def require_host_devices(
    request: Request, session: SessionDep, services: ServicesDep
) -> Session:
    """Manage in the path's project, and the host-devices right there (D86)."""
    project = str(request.path_params["project"])
    await _authorize(request, session, services.lxd, project, "manage")
    if not may_use_host_devices(session.user, project):
        raise HTTPException(status_code=403, detail=HOST_DENIED.format(project=project))
    return session


ProjectHostDevicesDep = Annotated[Session, Depends(require_host_devices)]
HOST_DENIED = "host paths and USB devices need the host-devices right in {project}"


async def get_hidden_paths(services: ServicesDep) -> list[str]:
    """The folders hidden from project admins (D87); not a policy."""
    return services.hostfs().hidden_paths


HiddenPathsDep = Annotated[list[str], Depends(get_hidden_paths)]


def check_host_devices(
    session: Session,
    project: str,
    new: dict[str, dict[str, str]],
    old: dict[str, dict[str, str]] | None = None,
    hidden_paths: list[str] | None = None,
) -> None:
    """Refuse adding or changing host devices without the right (D86): through the YAML
    editor, the create wizard or a profile, as much as through the host-devices tab. With
    it, a project admin still can't attach a hidden folder (D87)."""
    changed = host_device_changes(new, old or {})
    if not changed:
        return
    if not may_use_host_devices(session.user, project):
        raise HTTPException(
            status_code=403,
            detail=f"{', '.join(changed)}: " + HOST_DENIED.format(project=project),
        )
    if session.user.is_admin:
        return
    for name in changed:
        source = new[name].get("source", "")
        host_path = new[name].get("type") == "disk" and source.startswith("/")
        if host_path and exposes_hidden(source, hidden_paths or []):
            raise HTTPException(
                status_code=403,
                detail=f"{name}: {source} is, or contains, a folder hidden from project admins"
                " (Settings → Host devices)",
            )


class InBody:
    """For routes whose project is in the request body (creates): call ``check`` with it."""

    def __init__(self, request: Request, session: Session, lxd: LXDClient | None) -> None:
        self.request, self.session, self.lxd = request, session, lxd

    async def check(self, project: str, action: Action = "manage") -> None:
        await _authorize(self.request, self.session, self.lxd, project, action)


@_policy("project-in-body")
async def get_in_body(request: Request, session: SessionDep, services: ServicesDep) -> InBody:
    return InBody(request, session, services.lxd)


InBodyDep = Annotated[InBody, Depends(get_in_body)]
