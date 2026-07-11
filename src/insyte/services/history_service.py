"""Query-history application service — shared by TUI, MCP, and Studio."""

from __future__ import annotations

from insyte.metadata.repository import MetadataRepository
from insyte.query.models import QueryHistoryEntry, SecurityEventEntry


class HistoryService:
    """Read the audit log (query history and security events)."""

    def __init__(self, metadata: MetadataRepository) -> None:
        self._metadata = metadata

    def queries(self, limit: int = 20) -> list[QueryHistoryEntry]:
        return self._metadata.list_query_history(limit)

    def events(self, limit: int = 20) -> list[SecurityEventEntry]:
        return self._metadata.list_security_events(limit)
