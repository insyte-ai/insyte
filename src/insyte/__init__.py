"""Insyte — local-first AI analytics over your database, safely.

Insyte connects to a database using read-only credentials and lets you analyse it
through Claude Code, Codex, other MCP clients, or its own terminal UI. AI models never
receive database credentials and can never bypass the SQL validation pipeline.
"""

from __future__ import annotations

__version__ = "0.2.19"

__all__ = ["__version__"]
