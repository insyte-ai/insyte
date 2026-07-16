"""Insyte Studio — the browser-based analytics workspace (FastAPI backend)."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from insyte.studio.app import create_studio_app

__all__ = ["create_studio_app"]


def __getattr__(name: str) -> object:
    """Load the app factory lazily so schema-only imports do not initialize FastAPI."""

    if name == "create_studio_app":
        from insyte.studio.app import create_studio_app

        return create_studio_app
    raise AttributeError(name)
