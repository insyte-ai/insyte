"""Structured PDF export for complete Insyte analysis results."""

from __future__ import annotations

import io
from datetime import UTC, datetime
from typing import Any

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def result_to_pdf(result: dict[str, Any]) -> bytes:
    """Render every available analysis section and the complete result table."""

    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=36,
        leftMargin=36,
        topMargin=42,
        bottomMargin=42,
        title="Insyte Analysis Report",
    )
    styles = getSampleStyleSheet()
    story: list[Any] = [
        Paragraph("Insyte Analysis Report", styles["Title"]),
        Paragraph(
            datetime.now(UTC).strftime("Generated %Y-%m-%d %H:%M UTC"), styles["Normal"]
        ),
        Spacer(1, 16),
    ]

    _section(story, styles, "Summary", result.get("summary"))
    _section(story, styles, "Executive analysis", _report_text(result, "executive_summary"))
    report = result.get("report") or {}
    for title, key in (
        ("Key insights", "key_insights"),
        ("Evidence", "evidence"),
        ("Confidence", "confidence_reasons"),
        ("Root cause", "root_cause"),
        ("Business impact", "business_impact"),
        ("Forecast", "forecast"),
        ("Risks", "risks"),
        ("Data quality", "data_quality"),
        ("Caveats", "caveats"),
        ("Recommendations", "recommendations"),
        ("Metrics to track", "metrics_to_track"),
        ("Next best questions", "next_best_questions"),
    ):
        _section(story, styles, title, report.get(key))

    charts = result.get("charts") or []
    if charts:
        story.append(Paragraph("Charts and visual data", styles["Heading2"]))
        for chart in charts:
            story.append(
                Paragraph(
                    str(chart.get("title") or chart.get("type") or "Chart"),
                    styles["Heading3"],
                )
            )
            _chart_table(story, styles, chart)

    query = result.get("query") or {}
    if query.get("sql"):
        _section(story, styles, "SQL and data sources", query.get("sql"))
        _section(story, styles, "Referenced tables", query.get("referenced_tables"))

    table = result.get("table") or {}
    columns, rows = table.get("columns") or [], table.get("rows") or []
    if columns:
        story.append(Paragraph("Complete result data", styles["Heading2"]))
        data = [columns] + [[_cell(value) for value in row] for row in rows]
        tab = Table(data, repeatRows=1, splitByRow=1)
        tab.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#25304a")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cccccc")),
            ("FONTSIZE", (0, 0), (-1, -1), 7),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(tab)

    document.build(story)
    return buffer.getvalue()


def _section(story: list[Any], styles: Any, title: str, value: Any) -> None:
    if value in (None, "", [], {}):
        return
    story.append(Paragraph(title, styles["Heading2"]))
    if isinstance(value, list):
        for item in value:
            story.append(Paragraph(f"• {_cell(item)}", styles["BodyText"]))
    elif isinstance(value, dict):
        for key, item in value.items():
            story.append(Paragraph(f"<b>{key}:</b> {_cell(item)}", styles["BodyText"]))
    else:
        story.append(Paragraph(_cell(value), styles["BodyText"]))
    story.append(Spacer(1, 8))


def _report_text(result: dict[str, Any], key: str) -> Any:
    return (result.get("report") or {}).get(key)


def _chart_table(story: list[Any], styles: Any, chart: dict[str, Any]) -> None:
    points = chart.get("data") or []
    x_key = chart.get("x_key")
    series = chart.get("series") or []
    value_key = series[0].get("key") if series else None
    labels = [point.get(x_key) for point in points]
    values = [point.get(value_key) for point in points]
    if not labels or not values:
        return
    numeric = []
    for value in values:
        try:
            numeric.append(float(value))
        except (TypeError, ValueError):
            numeric.append(0.0)
    drawing = Drawing(500, 190)
    left, bottom, width, height = 45, 30, 430, 125
    maximum = max(max(abs(value) for value in numeric), 1.0)
    drawing.add(Line(left, bottom, left + width, bottom, strokeColor=colors.grey))
    drawing.add(Line(left, bottom, left, bottom + height, strokeColor=colors.grey))
    chart_type = str(chart.get("type") or "line").lower()
    if chart_type in {"bar", "column"}:
        bar_width = max(4, width / max(len(numeric), 1) * 0.65)
        for index, value in enumerate(numeric):
            x = left + (index + 0.5) * width / len(numeric)
            bar_height = value / maximum * height
            drawing.add(
                Rect(
                    x - bar_width / 2,
                    bottom,
                    bar_width,
                    bar_height,
                    fillColor=colors.HexColor("#5968ff"),
                    strokeColor=None,
                )
            )
    else:
        previous = None
        for index, value in enumerate(numeric):
            x = left + index * width / max(len(numeric) - 1, 1)
            y = bottom + value / maximum * height
            if previous is not None:
                drawing.add(
                    Line(
                        previous[0], previous[1], x, y,
                        strokeColor=colors.HexColor("#5968ff"), strokeWidth=2,
                    )
                )
            previous = (x, y)
    for index, label in enumerate(labels[:12]):
        x = left + index * width / max(len(labels) - 1, 1)
        drawing.add(String(x - 12, 12, str(label)[:14], fontSize=7))
    drawing.add(
        String(
            left,
            bottom + height + 8,
            str(value_key or "Value"),
            fontSize=8,
            fillColor=colors.HexColor("#303b72"),
        )
    )
    story.append(drawing)
    story.append(Spacer(1, 6))
    data = [["Label", "Value"]] + [
        [_cell(label), _cell(value)] for label, value in zip(labels, values, strict=False)
    ]
    tab = Table(data, repeatRows=1, colWidths=[3.5 * inch, 2 * inch])
    tab.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4c5cff")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
    ]))
    story.append(tab)
    story.append(Spacer(1, 8))


def _cell(value: Any) -> str:
    return (
        str(value if value is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
