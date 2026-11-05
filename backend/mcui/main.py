"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from mcui import __version__
from mcui.api import auth, cluster, metrics, ops
from mcui.config import Settings, load_settings
from mcui.lxd.client import LXDError, LXDUnavailable
from mcui.services import Services, build_services

DEFAULT_STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


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
