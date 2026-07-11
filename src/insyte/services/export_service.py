"""Export analysis results (CSV for now; PNG/HTML render client-side)."""

from __future__ import annotations

import csv
import io


def result_table_to_csv(result: dict) -> str:
    """Render an analysis result's data table as CSV text.

    ``result`` is the stored structured :class:`AnalysisResult` (as a dict).
    """

    table = result.get("table") or {}
    columns = table.get("columns") or []
    rows = table.get("rows") or []

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    if columns:
        writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if cell is None else cell for cell in row])
    return buffer.getvalue()
