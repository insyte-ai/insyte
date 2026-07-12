from __future__ import annotations

from insyte.studio.context import ChatContext, build_chat_context
from insyte.studio.schemas import AnalysisResult, Contributor, DataFreshness, MetricCard


def test_chat_context_roundtrip_and_prompt_summary() -> None:
    context = ChatContext(
        active_metric="revenue",
        active_dimension="city",
        active_period="last_month",
        last_analysis_id="an_1",
        last_result_summary="Revenue by city.",
        unresolved_assumptions=["Metadata was stale."],
    )
    restored = ChatContext.from_dict(context.to_dict())

    assert restored.active_metric == "revenue"
    assert "active_dimension=city" in restored.prompt_summary()
    assert "Metadata was stale" in restored.prompt_summary()


def test_build_chat_context_compresses_result_and_recent_turns() -> None:
    result = AnalysisResult(
        analysis_id="an_2",
        summary="Revenue by city: Mumbai leads.",
        metrics=[MetricCard(label="Revenue", value=1000.0, format="currency")],
        contributors=[
            Contributor(label="Mumbai", absolute_change=700.0, contribution_percent=70.0)
        ],
        freshness=DataFreshness(mode="direct"),
    )

    context = build_chat_context(
        question="revenue by city",
        result=result,
        active_metric="revenue",
        active_dimension="city",
        active_period="last_month",
        detailed=True,
    )

    assert context.active_metric == "revenue"
    assert context.active_dimension == "city"
    assert context.active_report_mode == "detailed"
    assert context.last_analysis_id == "an_2"
    assert "top contributor Mumbai" in (context.last_result_summary or "")
    assert len(context.recent_turns) == 2
