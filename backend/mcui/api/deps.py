"""Request dependencies: services, session cookie, CSRF and role checks."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request

from mcui.auth.store import Session, Store
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


SessionDep = Annotated[Session, Depends(current_session)]


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
