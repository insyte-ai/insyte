"""Export analysis results as portable tabular files."""

from __future__ import annotations

import csv
import io
from copy import copy

from openpyxl import Workbook


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


def result_table_to_xlsx(result: dict) -> bytes:
    """Render an analysis result's data table as an Excel workbook."""

    table = result.get("table") or {}
    columns = table.get("columns") or []
    rows = table.get("rows") or []
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Insyte result"
    if columns:
        sheet.append(columns)
        for cell in sheet[1]:
            font = copy(cell.font)
            font.bold = True
            cell.font = font
    for row in rows:
        sheet.append(["" if cell is None else cell for cell in row])
    sheet.freeze_panes = "A2" if columns else None
    sheet.auto_filter.ref = sheet.dimensions if columns and rows else None
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
