"""A one-line status bar showing project/connection context."""

from __future__ import annotations

from textual.widgets import Static


class StatusBar(Static):
    """Displays database, table count, analytics mode and data freshness."""

    def __init__(self, text: str) -> None:
        super().__init__(text, id="status-bar")

    def update_status(self, text: str) -> None:
        self.update(text)
