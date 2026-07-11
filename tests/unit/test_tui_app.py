"""Textual smoke tests for the Insyte app (fake controller, no database)."""

from __future__ import annotations

from insyte.analytics.models import AnalysisKind, AnalysisResult, ChartSpec, ChartType
from insyte.tui.app import InsyteApp
from insyte.tui.controller import Response, ResponseKind
from insyte.tui.widgets.result_card import ResultCard
from insyte.tui.widgets.table_panel import TablePanel


class FakeController:
    def run(self, text: str) -> Response:
        if text == "/help":
            return Response.message("help here")
        if text == "/metrics":
            return Response(
                ResponseKind.table, title="Metrics", columns=["Name"], rows=[["revenue"]]
            )
        if text == "/clear":
            return Response(ResponseKind.clear)
        return Response(
            ResponseKind.analysis,
            analysis=AnalysisResult(
                kind=AnalysisKind.segment,
                metric="revenue",
                label="Revenue",
                columns=["segment", "value"],
                rows=[("A", 10), ("B", 5)],
                formatted_rows=[["A", "10"], ["B", "5"]],
                sql="SELECT ...",
                chart=ChartSpec(ChartType.bar, title="Revenue"),
                summary="A leads",
                row_count=2,
                duration_ms=3.0,
            ),
        )


def _app() -> InsyteApp:
    return InsyteApp(FakeController(), "demo", "PostgreSQL · 7 tables")


async def test_app_mounts() -> None:
    async with _app().run_test() as pilot:
        await pilot.pause()
        assert type(pilot.app.screen).__name__ == "ChatScreen"


async def test_query_produces_result_card() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press(*"revenue by city")
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert len(app.screen.query(ResultCard)) == 1


async def test_slash_metrics_produces_table() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press(*"/metrics")
        await pilot.press("enter")
        await pilot.pause(0.3)
        assert len(app.screen.query(TablePanel)) >= 1


async def test_clear_removes_messages() -> None:
    app = _app()
    async with app.run_test() as pilot:
        await pilot.press(*"/help")
        await pilot.press("enter")
        await pilot.pause(0.2)
        await pilot.press(*"/clear")
        await pilot.press("enter")
        await pilot.pause(0.2)
        log = app.screen.query_one("#chat-log")
        assert len(log.children) == 0
