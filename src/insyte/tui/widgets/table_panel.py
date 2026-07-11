"""A titled data table used for query results and metadata listings."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static


class TablePanel(Vertical):
    """Renders columns/rows as a DataTable, with an optional title."""

    def __init__(
        self,
        columns: list[str],
        rows: list[list[str]],
        *,
        title: str | None = None,
    ) -> None:
        super().__init__(classes="table-panel")
        self._columns = columns
        self._rows = rows
        self._title = title

    def compose(self) -> ComposeResult:
        if self._title:
            yield Static(f"[b]{self._title}[/b]", classes="panel-title")
        yield DataTable(zebra_stripes=True, cursor_type="none")

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns(*self._columns)
        for row in self._rows[:500]:
            table.add_row(*[_cell(value) for value in row])


def _cell(value: object) -> str:
    return "" if value is None else str(value)
