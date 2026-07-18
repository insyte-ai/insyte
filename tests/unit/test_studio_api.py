"""Unit tests for the Studio FastAPI backend (Stage 10.2)."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from insyte.analytics.models import (
    AnalysisKind,
    AnalysisResult,
    ChartSpec,
    ChartType,
    PeriodComparison,
)
from insyte.config import loader, paths
from insyte.config.models import InsyteConfig, ProjectSection
from insyte.connectors.base import (
    ConnectionCheckResult,
    PermissionReport,
    ServerInfo,
    SSLInfo,
)
from insyte.exceptions import QueryValidationError
from insyte.metadata.models import (
    ScannedColumn,
    ScannedTable,
    ScanResult,
    TableCategory,
    TableKind,
)
from insyte.metadata.repository import MetadataRepository
from insyte.semantic.models import StarterQuestion
from insyte.semantic.repository import SemanticRepository
from insyte.services.project_service import ProjectService
from insyte.services.setup_service import SetupService
from insyte.studio.app import create_studio_app

_FIXTURE_SEMANTIC = Path(__file__).parent.parent / "fixtures" / "semantic.yaml"


def _setup_project(name: str = "demo") -> None:
    loader.create_project(InsyteConfig(project=ProjectSection(name=name)))
    repo = MetadataRepository(paths.metadata_path(name))
    now = datetime.now(UTC)
    repo.save_scan(
        ScanResult(
            schemas={"public": None},
            tables=[
                ScannedTable(
                    schema="public",
                    name="orders",
                    kind=TableKind.table,
                    columns=[
                        ScannedColumn("id", 0, "integer", nullable=False, is_primary_key=True),
                        ScannedColumn("total_amount", 1, "numeric", nullable=True),
                    ],
                    primary_key_columns=["id"],
                    category=TableCategory.fact,
                    category_confidence=0.8,
                )
            ],
            relationships=[],
        ),
        started_at=now,
        finished_at=now,
    )
    repo.dispose()
    shutil.copy(_FIXTURE_SEMANTIC, paths.semantic_path(name))
    semantic_repo = SemanticRepository(paths.semantic_path(name))
    layer = semantic_repo.load()
    layer.starter_questions = [
        StarterQuestion(
            question="Which city contributes most to completed revenue?",
            metric="completed_revenue",
            mode="segment",
            dimension="city",
            generated_by="codex",
        )
    ]
    semantic_repo.save(layer)


class FakeConnector:
    def check_connection(self) -> ConnectionCheckResult:
        return ConnectionCheckResult(
            server=ServerInfo(
                version="PostgreSQL 16",
                is_postgres=True,
                database="analytics",
                user="reader",
            ),
            ssl=SSLInfo(in_use=True, protocol="TLSv1.3"),
            permissions=PermissionReport(False, False, False, False),
            read_only_enforced=True,
            statement_timeout_seconds=20,
            lock_timeout_seconds=3,
        )

    def dispose(self) -> None:
        pass


class FakeAnalysis:
    def __init__(self, blocked: bool = False) -> None:
        self.blocked = blocked

    def _result(self) -> AnalysisResult:
        return AnalysisResult(
            kind=AnalysisKind.aggregate,
            metric="payment_failure_rate",
            label="Payment failure rate",
            columns=["value"],
            rows=[(0.333,)],
            formatted_rows=[["33.3%"]],
            sql="SELECT AVG(...) FROM public.payments",
            chart=ChartSpec(ChartType.none, title="Payment failure rate"),
            summary="Payment failure rate: 33.3%.",
            row_count=1,
            duration_ms=5.0,
        )

    def aggregate(self, metric, period=None):
        if self.blocked:
            raise QueryValidationError(["Access to blocked column 'x' is not allowed."])
        return self._result()

    def segment(self, metric, dimension, period=None, limit=20):
        return self._result()

    def segment_compare(self, metric, dimension, current, baseline, limit=20):
        return AnalysisResult(
            kind=AnalysisKind.segment,
            metric=metric,
            label="Payment failure rate",
            columns=[
                "segment",
                "current_value",
                "baseline_value",
                "absolute_change",
                "contribution_percent",
            ],
            rows=[("card", 0.4, 0.2, 0.2, 100)],
            formatted_rows=[["card", "40.0%", "20.0%", "20.0%", "100.0%"]],
            sql="WITH current_segments AS (...)",
            chart=ChartSpec(ChartType.bar, title="Payment failure rate"),
            summary=(
                f"Payment failure rate change by {dimension}: 'card' moved 20.0% "
                f"from {baseline.label} to {current.label}."
            ),
            row_count=1,
            duration_ms=5.0,
        )

    def opportunity(self, primary_metric, secondary_metric, dimension, period=None, limit=20):
        return AnalysisResult(
            kind=AnalysisKind.opportunity,
            metric=primary_metric,
            label="Margin rate high / Units sold low",
            columns=["segment", "primary_value", "secondary_value", "opportunity_score"],
            rows=[("Bengaluru", 0.42, 12, 0.91)],
            formatted_rows=[["Bengaluru", "42.0%", "12", "91%"]],
            sql="WITH segments AS (...)",
            chart=ChartSpec(ChartType.bar, title="Opportunity"),
            summary=(
                "Top city opportunity: 'Bengaluru' has margin rate of 42.0% with units sold of 12."
            ),
            row_count=1,
            duration_ms=5.0,
        )

    def timeseries(self, metric, grain, period=None):
        return AnalysisResult(
            kind=AnalysisKind.timeseries,
            metric=metric,
            label="Payment failure rate",
            columns=["period", "value"],
            rows=[
                (datetime(2026, 5, 1, tzinfo=UTC), 0.2),
                (datetime(2026, 6, 1, tzinfo=UTC), 0.333),
            ],
            formatted_rows=[["2026-05-01", "20.0%"], ["2026-06-01", "33.3%"]],
            sql="SELECT date_trunc('month', payment_ts), AVG(...) FROM public.payments",
            chart=ChartSpec(ChartType.line, title="Payment failure rate"),
            summary="Payment failure rate by month: 2 buckets; latest 33.3%.",
            row_count=2,
            duration_ms=5.0,
        )

    def compare(self, metric, current, baseline):
        return PeriodComparison(
            metric=metric,
            label="Payment failure rate",
            current=current,
            baseline=baseline,
            current_value=0.333,
            baseline_value=0.2,
            absolute_change=0.133,
            percent_change=66.5,
            sql_current="SELECT AVG(...) FROM public.payments WHERE payment_ts >= ...",
            sql_baseline="SELECT AVG(...) FROM public.payments WHERE payment_ts >= ...",
            summary="Payment failure rate increased by 66.5% from previous month to current month.",
        )


def _factory(blocked: bool = False):
    def factory():
        return FakeAnalysis(blocked), FakeConnector()

    return factory


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests hermetic: never shell out to a real claude/codex CLI on the dev machine."""

    from insyte.agents import planner

    monkeypatch.setenv("INSYTE_STUDIO_LLM", "off")
    monkeypatch.setattr(planner, "_run", lambda *_args, **_kwargs: None)


@pytest.fixture
def client(isolated_home: Path):
    _setup_project()
    services = ProjectService.open("demo")
    app = create_studio_app(services=services, analysis_factory=_factory())
    with TestClient(app) as test_client:
        yield test_client
    services.dispose()


def _final_result(sse_text: str) -> dict:
    for block in sse_text.split("\n\n"):
        if "event: response_completed" in block:
            data_line = next(line for line in block.splitlines() if line.startswith("data:"))
            return json.loads(data_line[len("data:") :].strip())["result"]
    raise AssertionError("no response_completed event")


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok"}


def test_bootstrap_studio_starts_without_project() -> None:
    app = create_studio_app(bootstrap=True)
    with TestClient(app) as bootstrap_client:
        body = bootstrap_client.get("/api/setup/status").json()
        assert body["needs_setup"] is True
        assert body["projects"] == []
        assert bootstrap_client.get("/api/status").status_code == 409


def test_browser_setup_creates_and_activates_project() -> None:
    app = create_studio_app(bootstrap=True)
    app.state.setup_service = SetupService(lambda _url, _config: FakeConnector())
    with TestClient(app) as bootstrap_client:
        token = bootstrap_client.get("/api/setup/status").json()["session_token"]
        response = bootstrap_client.post(
            "/api/setup/projects",
            headers={"x-insyte-session": token},
            json={
                "name": "rds-demo",
                "database_url": "postgresql://reader:secret@db.example.com:5432/analytics",
                "schemas": ["public"],
                "ssl_mode": "require",
                "ai_client": "codex",
            },
        )
        assert response.status_code == 200
        assert response.json()["connection"]["read_only_enforced"] is True
        assert bootstrap_client.get("/api/setup/status").json()["needs_setup"] is False
        public = bootstrap_client.get("/api/config/public").json()
        assert public["project"] == "rds-demo"
        assert "secret" not in json.dumps(public)


def test_disconnect_returns_to_setup_without_deleting_project(isolated_home: Path) -> None:
    _setup_project("keep-me")
    paths.set_active_project("keep-me")
    services = ProjectService.open("keep-me")
    app = create_studio_app(services=services)

    with TestClient(app) as test_client:
        token = test_client.get("/api/setup/status").json()["session_token"]
        response = test_client.post("/api/setup/disconnect", headers={"x-insyte-session": token})
        status = test_client.get("/api/setup/status").json()

    assert response.json() == {"disconnected": True, "projects": ["keep-me"]}
    assert status["needs_setup"] is True
    assert status["active_project"] is None
    assert loader.project_exists("keep-me")
    assert paths.get_active_project() is None


def test_saved_project_can_be_reopened_from_setup(isolated_home: Path) -> None:
    _setup_project("saved")
    app = create_studio_app(bootstrap=True)

    with TestClient(app) as test_client:
        token = test_client.get("/api/setup/status").json()["session_token"]
        response = test_client.post(
            "/api/setup/projects/saved/open", headers={"x-insyte-session": token}
        )
        status = test_client.get("/api/setup/status").json()

    assert response.json() == {"project": "saved"}
    assert status["needs_setup"] is False
    assert status["active_project"] == "saved"
    assert paths.get_active_project() == "saved"


def test_status(client: TestClient) -> None:
    body = client.get("/api/status").json()
    assert body["project"] == "demo"
    assert body["schema"]["tables"] == 1
    assert body["read_only"] is True


def test_config_public_redacts_secrets(client: TestClient) -> None:
    body = client.get("/api/config/public").json()
    text = json.dumps(body)
    assert "password" not in text.lower()
    assert "postgresql://" not in text
    assert body["database"]["url_env"] == "INSYTE_DATABASE_URL"  # name only


def test_schema_and_metrics(client: TestClient) -> None:
    schema = client.get("/api/schema").json()
    assert schema["table_count"] == 1
    metrics = client.get("/api/metrics").json()
    completed = next(m for m in metrics["metrics"] if m["name"] == "completed_revenue")
    assert completed["time_column"] == "orders.completed_at"
    assert metrics["starter_questions"][0]["question"] == (
        "Which city contributes most to completed revenue?"
    )


def test_metric_approval_clears_confirmation_requirement(client: TestClient) -> None:
    repo = SemanticRepository(paths.semantic_path("demo"))
    layer = repo.load()
    layer.metrics["order_count"].requires_confirmation = True
    layer.metrics["order_count"].assumption = "Completed means status completed."
    repo.save(layer)

    response = client.post("/api/metrics/order_count/approve")

    assert response.status_code == 200
    assert response.json()["requires_confirmation"] is False
    assert (
        SemanticRepository(paths.semantic_path("demo")).load().metrics["order_count"].status.value
        == "confirmed"
    )


def test_conversation_and_analysis_flow(client: TestClient) -> None:
    conv = client.post("/api/conversations", json={"title": "Test"}).json()
    assert conv["id"].startswith("conv_")

    posted = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "payment failure rate"},
    ).json()
    analysis_id = posted["analysis_id"]
    assert posted["stream_url"].endswith("/events")

    events = client.get(posted["stream_url"]).text
    assert "event: question_received" in events
    assert "event: response_completed" in events
    result = _final_result(events)
    assert result["status"] == "completed"
    assert result["metrics"][0]["value"] == pytest.approx(0.333)

    # Persisted and retrievable.
    stored = client.get(f"/api/analyses/{analysis_id}").json()
    assert stored["summary"] == "Payment failure rate: 33.3%."

    # The assistant message was appended.
    convo = client.get(f"/api/conversations/{conv['id']}").json()
    assert [m["role"] for m in convo["messages"]] == ["user", "assistant"]


def test_ambiguous_qualifier_saves_blocked_metric_proposal(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from insyte.metadata.models import CardinalityCategory, ColumnProfile
    from insyte.nl.llm import Backend, NLResolution
    from insyte.semantic.proposals import DerivedMetricProposal
    from insyte.studio import events

    proposal = DerivedMetricProposal(
        name="positive_review_count",
        label="Positive review count",
        base_metric="order_count",
        filter_column="orders.status",
        filter_values=("completed",),
        aliases=("positive feedback",),
        assumption="Completed orders represent positive feedback.",
        confidence=0.8,
        evidence=("metric:order_count", "profile:public.orders.status"),
    )
    monkeypatch.setattr(events, "available_backends", lambda _pref: [Backend("codex", [])])
    monkeypatch.setattr(
        events,
        "resolve",
        lambda *args, **kwargs: NLResolution(
            "clarification",
            text="This definition needs confirmation.",
            metric="order_count",
            proposal=proposal,
        ),
    )
    monkeypatch.setattr(
        events.SchemaService,
        "column_profiles",
        lambda _self: [
            ColumnProfile(
                schema="public",
                table="orders",
                column="status",
                null_fraction=0,
                distinct_estimate=1,
                duplicate_ratio=0.9,
                cardinality=CardinalityCategory.constant,
                sampled_rows=10,
                top_values=[("completed", 10)],
            )
        ],
    )

    conv = client.post("/api/conversations", json={"title": "Qualifier"}).json()
    posted = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "Show positive feedback trend"},
    ).json()
    result = _final_result(client.get(posted["stream_url"]).text)

    assert result["status"] == "clarification"
    layer = SemanticRepository(paths.semantic_path("demo")).load()
    assert layer.metrics["positive_review_count"].requires_confirmation is True
    assert layer.metrics["positive_review_count"].filters == {"orders.status": ["completed"]}


def test_investigation_question_streams_timeline(client: TestClient) -> None:
    conv = client.post("/api/conversations", json={"title": "Investigation"}).json()
    posted = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "why did payment failure rate increase"},
    ).json()
    events = client.get(posted["stream_url"]).text

    assert "event: investigation_planned" in events
    assert "event: investigation_step_started" in events
    assert "event: investigation_step_completed" in events
    assert "event: investigation_report_ready" in events
    result = _final_result(events)
    assert result["status"] == "completed"
    assert result["investigation"]["plan"]["metric"] == "payment_failure_rate"
    assert [step["status"] for step in result["investigation"]["plan"]["steps"]]
    assert "Investigation for payment failure rate" in result["summary"]


def test_investigation_uses_explicit_historical_periods(client: TestClient) -> None:
    conv = client.post("/api/conversations", json={"title": "Investigation"}).json()
    posted = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={
            "content": ("Why did payment failure rate increase from February 2026 to March 2026?")
        },
    ).json()
    result = _final_result(client.get(posted["stream_url"]).text)
    plan = result["investigation"]["plan"]

    assert plan["period"] == "Mar 2026 vs Feb 2026"
    assert plan["current_period"]["label"] == "Mar 2026"
    assert plan["baseline_period"]["label"] == "Feb 2026"
    assert "Mar 2026" in plan["steps"][1]["title"]
    assert any(
        "from Feb 2026 to Mar 2026" in finding for finding in result["investigation"]["findings"]
    )


def test_investigation_is_saved_and_routeable(client: TestClient) -> None:
    conv = client.post("/api/conversations", json={"title": "Investigation"}).json()
    posted = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "why did payment failure rate increase"},
    ).json()
    client.get(posted["stream_url"])

    saved = client.get("/api/investigations").json()["investigations"]
    assert len(saved) == 1
    assert saved[0]["id"].startswith("inv_")
    assert saved[0]["analysis_id"] == posted["analysis_id"]
    assert saved[0]["conversation_id"] == conv["id"]
    assert "payment failure rate" in saved[0]["title"].lower()

    detail = client.get(f"/api/investigations/{saved[0]['id']}").json()["investigation"]
    assert detail["result"]["analysis_id"] == posted["analysis_id"]
    assert detail["result"]["investigation"]["plan"]["metric"] == "payment_failure_rate"

    renamed = client.post(
        f"/api/investigations/{saved[0]['id']}/rename",
        json={"title": "Failure-rate investigation"},
    ).json()
    assert renamed == {"renamed": True, "title": "Failure-rate investigation"}
    assert (
        client.get(f"/api/investigations/{saved[0]['id']}").json()["investigation"]["title"]
        == "Failure-rate investigation"
    )

    assert client.delete(f"/api/investigations/{saved[0]['id']}").json() == {"deleted": True}
    assert client.get("/api/investigations").json()["investigations"] == []


def test_detailed_investigation_attaches_report(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from insyte.nl.llm import Backend
    from insyte.studio import events, investigation
    from insyte.studio.schemas import DetailedReport

    seen_payloads = []

    def fake_report(payload, backend, **_kwargs):  # noqa: ANN001, ANN202
        seen_payloads.append(payload)
        return DetailedReport(
            tl_dr="Total amount increased because the latest month is higher.",
            evidence=["Trend and segment steps completed."],
            generated_by=backend.name,
        )

    monkeypatch.setattr(events, "available_backends", lambda _pref: [Backend("codex", ["codex"])])
    monkeypatch.setattr(investigation, "resolve_report", fake_report)
    conv = client.post("/api/conversations", json={"title": "Investigation"}).json()
    posted = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "why did payment failure rate increase", "detailed": True},
    ).json()
    text = client.get(posted["stream_url"]).text
    result = _final_result(text)

    assert "event: report_generating" in text
    assert "event: report_ready" in text
    assert result["investigation"] is not None
    assert result["report"]["generated_by"] == "codex"
    assert seen_payloads[0]["workflow"] == "investigation"
    assert seen_payloads[0]["computed_findings"]


def test_blocked_query_never_runs(isolated_home: Path) -> None:
    _setup_project()
    services = ProjectService.open("demo")
    app = create_studio_app(services=services, analysis_factory=_factory(blocked=True))
    with TestClient(app) as client:
        conv = client.post("/api/conversations", json={}).json()
        posted = client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "payment failure rate"},
        ).json()
        events = client.get(posted["stream_url"]).text
        assert "event: query_blocked" in events
        result = _final_result(events)
        assert result["status"] == "blocked"
        assert result["warnings"]
    services.dispose()


def test_unexpected_analysis_error_completes_stream(isolated_home: Path) -> None:
    class ExplodingAnalysis(FakeAnalysis):
        def aggregate(self, metric, period=None):  # noqa: ANN001, ANN202
            raise ModuleNotFoundError("simulated frozen-only import failure")

    _setup_project()
    services = ProjectService.open("demo")
    app = create_studio_app(
        services=services,
        analysis_factory=lambda: (ExplodingAnalysis(), FakeConnector()),
    )
    with TestClient(app) as test_client:
        conv = test_client.post("/api/conversations", json={}).json()
        posted = test_client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "payment failure rate"},
        ).json()
        events = test_client.get(posted["stream_url"]).text
        result = _final_result(events)

    assert result["status"] == "error"
    assert "could not be completed" in result["summary"]
    services.dispose()


def test_csv_export(client: TestClient) -> None:
    conv = client.post("/api/conversations", json={}).json()
    posted = client.post(
        f"/api/conversations/{conv['id']}/messages", json={"content": "payment failure rate"}
    ).json()
    client.get(posted["stream_url"])  # run + persist
    response = client.post(f"/api/analyses/{posted['analysis_id']}/exports/csv")
    assert response.status_code == 200
    assert "value" in response.text  # CSV header


def test_frontend_shell_served(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Insyte Studio" in response.text
    assert "/assets/app.js" in response.text


def test_spa_assets_served(client: TestClient) -> None:
    css = client.get("/assets/app.css")
    assert css.status_code == 200 and ":root" in css.text
    js = client.get("/assets/app.js")
    assert js.status_code == 200
    # The compiled SPA is present (guards the wheel bundling of studio_dist/assets).
    assert "EventSource" in js.text and "renderResult" in js.text
    assert 'investigation_planned: "Planning your investigation"' in js.text
    assert "Investigation timeline" in js.text
    assert "openChartFullscreen" in js.text
    assert "chart-tip" in js.text
    assert 'kind: spec.type === "line" ? "Trend" : "Breakdown"' in js.text
    assert 'class: "zero-line"' in js.text
    assert "bar-stop-top" in js.text
    assert "area-stop-top" in js.text
    assert "chartHeaderIcon" in js.text
    assert "maxChange" in js.text
    assert 'class: "g-zero"' in js.text
    assert "* 50" in js.text
    assert "niceGrowthLimit" in js.text
    assert 'class: "g-scale-axis"' in js.text
    assert "m.starter_questions" in js.text
    assert "generated.slice(0, 4)" in js.text
    assert "renderInvestigationsPage" in js.text
    assert "report-mode-btn" in js.text
    assert "exportMarkdown" in js.text


def test_spa_fallback_for_client_routes(client: TestClient) -> None:
    # Unknown (non-file) paths return index.html so client-side routing works.
    response = client.get("/some/deep/route")
    assert response.status_code == 200
    assert "Insyte Studio" in response.text


def test_bad_host_header_rejected(client: TestClient) -> None:
    response = client.get("/api/health", headers={"host": "evil.example.com"})
    assert response.status_code == 400


def test_unrecognised_question(client: TestClient) -> None:
    # Clearly unrelated input is rejected before any AI backend can answer it.
    conv = client.post("/api/conversations", json={}).json()
    posted = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "what is diamond problem in java"},
    ).json()
    result = _final_result(client.get(posted["stream_url"]).text)
    assert result["status"] == "out_of_scope"
    assert "connected business data" in result["summary"]
    assert "diamond" not in result["summary"].lower()


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("HI", "Hi!"),
        ("Good morning", "Good morning!"),
        ("what u can do", "I analyze the connected business data."),
    ],
)
def test_safe_builtin_conversation(client: TestClient, question: str, expected: str) -> None:
    conv = client.post("/api/conversations", json={}).json()
    posted = client.post(
        f"/api/conversations/{conv['id']}/messages", json={"content": question}
    ).json()
    result = _final_result(client.get(posted["stream_url"]).text)
    assert result["status"] == "message"
    assert expected in result["summary"]


def test_free_form_question_resolved_by_llm(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate a local AI CLI translating a free-form question into a metric intent.
    from insyte.nl.llm import Backend, NLResolution
    from insyte.studio import events
    from insyte.tui.intent import AnalysisMode

    monkeypatch.setattr(events, "available_backends", lambda _pref: [Backend("claude", ["claude"])])
    monkeypatch.setattr(
        events,
        "resolve",
        lambda *_a, **_k: NLResolution(
            "analysis", metric="completed_revenue", mode=AnalysisMode.aggregate
        ),
    )

    conv = client.post("/api/conversations", json={}).json()
    posted = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "roughly how much money did we make overall"},
    ).json()
    events_text = client.get(posted["stream_url"]).text
    assert "event: ai_resolving" in events_text
    result = _final_result(events_text)
    assert result["status"] == "completed"


def test_free_form_analytics_guidance_is_grounded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from insyte.nl.llm import Backend, NLResolution
    from insyte.studio import events

    monkeypatch.setattr(events, "available_backends", lambda _pref: [Backend("codex", ["codex"])])
    monkeypatch.setattr(
        events,
        "resolve",
        lambda *_a, **_k: NLResolution(
            "guidance", text="Compare completed revenue by city and inspect its monthly trend."
        ),
    )

    conv = client.post("/api/conversations", json={}).json()
    posted = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "how can we improve business performance"},
    ).json()
    result = _final_result(client.get(posted["stream_url"]).text)
    assert result["status"] == "guidance"
    assert "completed revenue" in result["summary"]


def test_free_form_forecast_projects_year(
    isolated_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from insyte.analytics.models import AnalysisKind
    from insyte.analytics.models import AnalysisResult as DomainResult
    from insyte.nl.llm import Backend, NLResolution
    from insyte.studio import events
    from insyte.tui.intent import AnalysisMode

    now = datetime.now(UTC)
    # A full prior year plus the completed months of the current year, all 100/month.
    rows = [(datetime(now.year - 1, m, 1, tzinfo=UTC), 100.0) for m in range(1, 13)]
    rows += [(datetime(now.year, m, 1, tzinfo=UTC), 100.0) for m in range(1, now.month)]

    class ForecastAnalysis:
        def timeseries(self, metric, grain, period=None):  # noqa: ANN001, ANN201
            return DomainResult(
                kind=AnalysisKind.timeseries,
                metric=metric,
                label="Revenue",
                columns=["period", "value"],
                rows=rows,
                formatted_rows=[[d.isoformat(), "100"] for d, _ in rows],
                sql="SELECT date_trunc('month', order_date), SUM(amt) FROM orders GROUP BY 1",
                chart=ChartSpec(ChartType.line, title="Revenue"),
                summary="",
                row_count=len(rows),
                duration_ms=1.0,
            )

    _setup_project()
    services = ProjectService.open("demo")
    app = create_studio_app(
        services=services,
        analysis_factory=lambda: (ForecastAnalysis(), FakeConnector()),
    )
    monkeypatch.setattr(events, "available_backends", lambda _pref: [Backend("claude", ["claude"])])
    monkeypatch.setattr(
        events,
        "resolve",
        lambda *_a, **_k: NLResolution(
            "analysis", metric="completed_revenue", mode=AnalysisMode.forecast
        ),
    )

    with TestClient(app) as client:
        conv = client.post("/api/conversations", json={}).json()
        posted = client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "what is the expected sales this year"},
        ).json()
        result = _final_result(client.get(posted["stream_url"]).text)

    assert result["status"] == "completed"
    assert result["projection"]["year"] == now.year
    assert result["projection"]["projected_total"] > 0
    assert any("projected" in m["label"].lower() for m in result["metrics"])
    assert result["limitations"]  # carries the "estimate, not a guarantee" caveat
    services.dispose()


def test_free_form_opportunity_runs_multi_metric_analysis(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from insyte.nl.llm import Backend, NLResolution
    from insyte.studio import events
    from insyte.tui.intent import AnalysisMode

    monkeypatch.setattr(events, "available_backends", lambda _pref: [Backend("claude", ["claude"])])
    monkeypatch.setattr(
        events,
        "resolve",
        lambda *_a, **_k: NLResolution(
            "analysis",
            metric="margin_rate",
            secondary_metric="units_sold",
            mode=AnalysisMode.opportunity,
            dimension="city",
        ),
    )

    conv = client.post("/api/conversations", json={}).json()
    posted = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "where are margins strong but sales volume is low"},
    ).json()
    events_text = client.get(posted["stream_url"]).text
    assert "event: query_started" in events_text
    result = _final_result(events_text)
    assert result["status"] == "completed"
    assert result["table"]["columns"] == [
        "segment",
        "primary_value",
        "secondary_value",
        "opportunity_score",
    ]
    assert "Bengaluru" in result["summary"]


def test_detailed_report_attaches(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """With a backend available and the toggle on, an analyst report attaches to the result."""

    from insyte.nl import llm
    from insyte.nl.llm import Backend
    from insyte.studio import events
    from insyte.studio.schemas import DetailedReport

    monkeypatch.setattr(events, "available_backends", lambda _pref: [Backend("codex", ["codex"])])
    monkeypatch.setattr(
        llm,
        "resolve_report",
        lambda payload, backend, **_k: DetailedReport(
            executive_summary="Failures are concentrated on one gateway.",
            generated_by=backend.name,
        ),
    )

    conv = client.post("/api/conversations", json={}).json()
    posted = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "payment failure rate", "detailed": True},
    ).json()
    text = client.get(posted["stream_url"]).text

    assert "event: report_generating" in text
    assert "event: report_ready" in text
    result = _final_result(text)
    assert result["report"]["generated_by"] == "codex"
    assert result["report"]["executive_summary"].startswith("Failures are concentrated")


def test_detailed_report_skipped_without_backend(client: TestClient) -> None:
    """No claude/codex installed → the analysis still completes, report is skipped, not fatal."""

    # The autouse _no_real_llm fixture sets INSYTE_STUDIO_LLM=off, so no backend is available.
    conv = client.post("/api/conversations", json={}).json()
    posted = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "payment failure rate", "detailed": True},
    ).json()
    text = client.get(posted["stream_url"]).text

    assert "event: report_skipped" in text
    result = _final_result(text)
    assert result["status"] == "completed"  # base analysis unaffected
    assert result["report"] is None
    assert any("no local AI CLI" in w for w in result["warnings"])


def test_detailed_report_off_by_default(client: TestClient) -> None:
    """Without the toggle, no report machinery runs at all."""

    conv = client.post("/api/conversations", json={}).json()
    posted = client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "payment failure rate"},
    ).json()
    text = client.get(posted["stream_url"]).text

    assert "event: report_generating" not in text
    assert "event: report_skipped" not in text
    assert _final_result(text)["report"] is None
