"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from mcui import __version__
from mcui.api import (
    acls,
    auth,
    catalog,
    ceph,
    cluster,
    files,
    hosts,
    instances,
    loadbalancers,
    manage,
    metrics,
    networks,
    ops,
    ovn,
    placement,
    registries,
    storage,
    streams,
    volumes,
)
from mcui.config import Settings, load_settings
from mcui.lxd.client import LXDError, LXDUnavailable
from mcui.services import Services, build_services

_HERE = Path(__file__).resolve().parent
REPO_STATIC_DIR = _HERE.parent.parent / "frontend" / "dist"  # a checkout after `make build`
PACKAGED_STATIC_DIR = _HERE / "static"  # inside the wheel (`make dist`, D63)


def default_static_dir(repo: Path = REPO_STATIC_DIR, packaged: Path = PACKAGED_STATIC_DIR) -> Path:
    """The built SPA: a checkout's frontend/dist, else the copy shipped in the wheel."""
    return repo if (repo / "index.html").is_file() else packaged


DEFAULT_STATIC_DIR = default_static_dir()


def create_app(
    settings: Settings | None = None,
    *,
    services: Services | None = None,
    static_dir: Path | None = DEFAULT_STATIC_DIR,
) -> FastAPI:
    """Build the app. Serves the built SPA from ``static_dir`` when it exists.

    With no arguments, settings come from ``$MCUI_CONFIG`` (see ``mcui serve``).
    """
    if services is None:
        services = build_services(settings or load_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await services.start()
        yield
        await services.aclose()

    app = FastAPI(title="microcloud-ui", version=__version__, lifespan=lifespan)
    app.state.services = services
    # project users (D81) stay locked out until every route enforces their grants
    app.state.project_access = False

    @app.exception_handler(LXDError)
    async def lxd_error(_: Request, exc: LXDError) -> JSONResponse:
        status = 503 if isinstance(exc, LXDUnavailable) else 502
        return JSONResponse({"detail": f"LXD: {exc}"}, status_code=status)

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        return {"status": "ok", "version": __version__, "lxd_configured": services.lxd is not None}

    app.include_router(auth.router)
    app.include_router(cluster.router)
    app.include_router(metrics.router)
    app.include_router(ops.router)
    app.include_router(instances.router)
    app.include_router(catalog.router)
    app.include_router(streams.router)
    app.include_router(files.router)
    app.include_router(manage.router)
    app.include_router(networks.router)
    app.include_router(acls.router)
    app.include_router(loadbalancers.router)
    app.include_router(placement.router)
    app.include_router(registries.router)
    app.include_router(storage.router)
    app.include_router(volumes.router)
    app.include_router(ceph.router)
    app.include_router(ovn.router)
    app.include_router(hosts.router)

    if static_dir is not None and (static_dir / "index.html").is_file():
        root = static_dir.resolve()

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            if path.startswith("api/"):
                raise HTTPException(status_code=404)
            candidate = (root / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)
            return FileResponse(root / "index.html")

    return app
