"""A panel showing the validated SQL for a result, with safety annotations."""

from __future__ import annotations

from textual.widgets import Static


class SQLPanel(Static):
    """Shows the executed SQL plus read-only / validation / timing annotations."""

    def __init__(
        self, sql: str, row_count: int, duration_ms: float, applied_limit: int | None
    ) -> None:
        annotations = (
            f"✓ Read-only   ✓ Validated   ✓ Limit: {applied_limit}\n"
            f"Execution: {duration_ms:.0f} ms · {row_count} rows"
        )
        super().__init__(f"{sql}\n\n{annotations}", markup=False, classes="sql")
