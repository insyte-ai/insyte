"""Serve the compiled React frontend (spec §17): API routes first, then the SPA fallback."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


def frontend_dir() -> Path:
    """Return the packaged frontend directory (``src/insyte/studio_dist``)."""

    return Path(__file__).resolve().parent.parent / "studio_dist"


def mount_frontend(app: FastAPI, directory: Path) -> None:
    """Mount static assets and a SPA fallback. Register AFTER all /api routes."""

    index = directory / "index.html"
    assets = directory / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="studio-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend_fallback(path: str) -> FileResponse:
        requested = directory / path
        if path and requested.is_file() and directory in requested.resolve().parents:
            return FileResponse(requested)
        return FileResponse(index)
