"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from mcui import __version__

DEFAULT_STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


def create_app(static_dir: Path | None = DEFAULT_STATIC_DIR) -> FastAPI:
    """Build the app. Serves the built SPA from ``static_dir`` when it exists."""
    app = FastAPI(title="microcloud-ui", version=__version__)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

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


app = create_app()
