"""Tests for the LLM natural-language resolver (subprocess is mocked — no real CLI)."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from insyte.analytics.models import TimeGrain
from insyte.nl import llm
from insyte.nl.llm import Backend, NLResolution, _extract_json, _validate, detect_backend, resolve
from insyte.semantic.models import Dimension, Metric, MetricFormat, SemanticLayer
from insyte.tui.intent import AnalysisMode


def _layer() -> SemanticLayer:
    return SemanticLayer(
        metrics={
            "total_grand_total": Metric(
                label="Total grand total",
                expression="SUM(orders.grand_total)",
                source_table="public.orders",
                time_column="orders.created_at",
                format=MetricFormat.currency,
            ),
            "order_count": Metric(
                label="Order count",
                expression="COUNT(*)",
                source_table="public.orders",
                time_column="orders.created_at",
            ),
        },
        dimensions={"city": Dimension(source="cities.name", label="City")},
    )


def _fake_run(stdout: str):
    def run(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return SimpleNamespace(stdout=stdout, stderr="", returncode=0)

    return run


# ---- JSON extraction ---------------------------------------------------------------------


def test_extract_json_ignores_surrounding_noise() -> None:
    raw = 'thinking...\nHere you go:\n{"kind": "message", "text": "hi"}\ndone.'
    assert _extract_json(raw) == {"kind": "message", "text": "hi"}


def test_extract_json_handles_braces_in_strings() -> None:
    raw = '{"kind": "message", "text": "use {curly} braces"}'
    assert _extract_json(raw) == {"kind": "message", "text": "use {curly} braces"}


def test_extract_json_returns_none_when_absent() -> None:
    assert _extract_json("no json here") is None


# ---- validation --------------------------------------------------------------------------


def test_validate_analysis_maps_fields() -> None:
    data = {
        "kind": "analysis",
        "metric": "total_grand_total",
        "mode": "aggregate",
        "period": "last_month",
    }
    res = _validate(data, _layer())
    assert res == NLResolution(
        "analysis",
        metric="total_grand_total",
        mode=AnalysisMode.aggregate,
        grain=None,
        dimension=None,
        period="last_month",
    )


def test_validate_segment_without_dimension_downgrades_to_aggregate() -> None:
    data = {"kind": "analysis", "metric": "order_count", "mode": "segment", "dimension": "nope"}
    res = _validate(data, _layer())
    assert res is not None and res.mode is AnalysisMode.aggregate and res.dimension is None


def test_validate_timeseries_defaults_grain_to_month() -> None:
    data = {"kind": "analysis", "metric": "order_count", "mode": "timeseries"}
    res = _validate(data, _layer())
    assert res is not None and res.grain is TimeGrain.month


def test_validate_unknown_metric_returns_none() -> None:
    data = {"kind": "analysis", "metric": "made_up_metric", "mode": "aggregate"}
    assert _validate(data, _layer()) is None


def test_validate_rejects_out_of_range_period() -> None:
    data = {"kind": "analysis", "metric": "order_count", "period": "since_forever"}
    res = _validate(data, _layer())
    assert res is not None and res.period is None


def test_validate_message() -> None:
    res = _validate({"kind": "message", "text": "Hi there!"}, _layer())
    assert res == NLResolution("message", text="Hi there!")


def test_validate_forecast_mode() -> None:
    data = {"kind": "analysis", "metric": "total_grand_total", "mode": "forecast"}
    res = _validate(data, _layer())
    assert (
        res is not None and res.mode is AnalysisMode.forecast and res.metric == "total_grand_total"
    )


# ---- end-to-end resolve (mocked subprocess) ----------------------------------------------


def test_resolve_parses_cli_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        llm.subprocess,
        "run",
        _fake_run(
            '{"kind":"analysis","metric":"total_grand_total","mode":"aggregate","period":"last_month"}'
        ),
    )
    res = resolve("total order value last month", _layer(), Backend("claude", ["claude", "-p"]))
    assert res is not None and res.metric == "total_grand_total" and res.period == "last_month"


def test_resolve_returns_none_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    monkeypatch.setattr(llm.subprocess, "run", boom)
    assert resolve("anything", _layer(), Backend("claude", ["claude"])) is None


def test_resolve_returns_none_when_no_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm.subprocess, "run", _fake_run("I could not help with that."))
    assert resolve("anything", _layer(), Backend("codex", ["codex", "exec"])) is None


# ---- backend detection -------------------------------------------------------------------


def test_detect_backend_off_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INSYTE_STUDIO_LLM", raising=False)
    assert detect_backend("off") is None


def test_detect_backend_prefers_available_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INSYTE_STUDIO_LLM", raising=False)
    monkeypatch.setattr(
        llm.shutil, "which", lambda name: "/usr/bin/codex" if name == "codex" else None
    )
    backend = detect_backend("auto")
    assert backend is not None and backend.name == "codex"


def test_detect_backend_none_when_no_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INSYTE_STUDIO_LLM", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    assert detect_backend("auto") is None


def test_env_overrides_preference(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INSYTE_STUDIO_LLM", "off")
    assert detect_backend("claude") is None


# ---- robust JSON extraction (codex prints extra objects) ---------------------------------


def test_extract_json_prefers_object_with_kind() -> None:
    raw = '{"session":"abc","cfg":true}\nthinking...\n{"kind":"analysis","metric":"order_count"}'
    assert _extract_json(raw) == {"kind": "analysis", "metric": "order_count"}


def test_extract_json_falls_back_to_last_object() -> None:
    raw = 'noise {"a":1} more {"b":2} end'
    assert _extract_json(raw) == {"b": 2}


def test_available_backends_orders_and_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    from insyte.nl.llm import available_backends

    monkeypatch.delenv("INSYTE_STUDIO_LLM", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda name: f"/bin/{name}")  # both present
    names = [b.name for b in available_backends("auto")]
    assert names == ["claude", "codex"]  # claude first, then fallback to codex


def test_codex_default_args_skip_git_repo_check(monkeypatch: pytest.MonkeyPatch) -> None:
    from insyte.nl.llm import available_backends

    monkeypatch.delenv("INSYTE_STUDIO_LLM", raising=False)
    monkeypatch.setattr(llm.shutil, "which", lambda name: "/bin/codex" if name == "codex" else None)
    backends = available_backends("auto")
    assert backends and backends[0].argv == ["codex", "exec", "--skip-git-repo-check"]
