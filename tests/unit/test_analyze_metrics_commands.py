"""Unit tests for ``insyte metrics`` and ``insyte analyze`` (fake engine)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from insyte.analytics.models import AnalysisKind, AnalysisResult, ChartSpec, ChartType
from insyte.cli import analyze_command
from insyte.cli.app import app
from insyte.config import loader, paths
from insyte.config.models import InsyteConfig, ProjectSection
from insyte.exceptions import MetricNotFoundError

runner = CliRunner()

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "semantic.yaml"


@pytest.fixture
def project(isolated_home: Path) -> InsyteConfig:
    config = InsyteConfig(project=ProjectSection(name="demo"))
    loader.create_project(config)
    shutil.copy(_FIXTURE, paths.semantic_path("demo"))
    return config


def test_metrics_lists_semantic_layer(project: InsyteConfig) -> None:
    out = runner.invoke(app, ["metrics"])
    assert out.exit_code == 0
    assert "completed_revenue" in out.stdout
    assert "Dimensions" in out.stdout
    assert "city" in out.stdout


def test_metrics_empty(isolated_home: Path) -> None:
    loader.create_project(InsyteConfig(project=ProjectSection(name="bare")))
    out = runner.invoke(app, ["metrics", "--project", "bare"])
    assert out.exit_code == 0
    assert "No metrics defined" in out.stdout


class FakeEngine:
    def __init__(self, result: AnalysisResult) -> None:
        self._result = result

    def aggregate(self, metric, period=None):
        return self._result

    def timeseries(self, metric, grain, period=None):
        return self._result

    def segment(self, metric, dimension, period=None, limit=20):
        return self._result


def _analysis() -> AnalysisResult:
    return AnalysisResult(
        kind=AnalysisKind.segment,
        metric="completed_revenue",
        label="Completed revenue",
        columns=["segment", "value"],
        rows=[("Bengaluru", 400), ("Mumbai", 200)],
        formatted_rows=[["Bengaluru", "400"], ["Mumbai", "200"]],
        sql="SELECT ...",
        chart=ChartSpec(ChartType.bar, title="Completed revenue"),
        summary="Completed revenue by city: 'Bengaluru' leads.",
        row_count=2,
        duration_ms=5.0,
    )


def test_analyze_segment(project: InsyteConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analyze_command, "_build_engine", lambda cfg, rec: FakeEngine(_analysis()))
    out = runner.invoke(app, ["analyze", "completed_revenue", "--by", "city"])
    assert out.exit_code == 0, out.stdout
    assert "Bengaluru" in out.stdout
    assert "leads" in out.stdout


def test_analyze_metric_not_found(project: InsyteConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    class Boom:
        def aggregate(self, *a, **k):
            raise MetricNotFoundError("ghost")

    monkeypatch.setattr(analyze_command, "_build_engine", lambda cfg, rec: Boom())
    out = runner.invoke(app, ["analyze", "ghost"])
    assert out.exit_code == 1
    assert "not defined" in out.stdout
