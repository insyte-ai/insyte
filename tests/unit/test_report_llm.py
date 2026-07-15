"""Unit tests for the detailed-report LLM path (nl/llm.py) — no real CLI is ever spawned."""

from __future__ import annotations

import json

import pytest

from insyte.nl import llm
from insyte.nl.llm import (
    Backend,
    _extract_report_json,
    _validate_report,
    build_report_prompt,
    resolve_report,
)
from insyte.studio.schemas import DetailedReport

_GOOD = {
    "executive_summary": "Revenue is concentrated in Mumbai.",
    "key_insights": [
        {"title": "Concentration", "detail": "Top city is 70%.", "confidence": "high"}
    ],
    "recommendations": [{"action": "Diversify", "horizon": "short", "priority": "high"}],
    "confidence_overall": "high",
}


def _prompt_schema() -> dict:
    prompt = build_report_prompt({"metric": {"name": "total_amount"}})
    schema_text = prompt.split("## Exact output schema", 1)[1].split("```json", 1)[1]
    return json.loads(schema_text.split("```", 1)[0])


def test_report_skill_preserves_exact_response_contract() -> None:
    schema = _prompt_schema()
    expected = set(DetailedReport.model_fields) - {"generated_by"}
    assert set(schema) == expected
    assert set(schema["key_insights"][0]) == {
        "title",
        "detail",
        "evidence",
        "confidence",
        "limitations",
        "alternative_explanation",
    }
    assert set(schema["data_quality"][0]) == {"issue", "severity", "affected", "impact"}
    assert set(schema["root_cause"]) == {
        "what_changed",
        "when",
        "dimension",
        "likely_cause",
        "confidence",
        "evidence",
    }
    assert set(schema["business_impact"]) == {"narrative", "financial_note"}
    assert set(schema["forecast"]) == {
        "expected",
        "best_case",
        "worst_case",
        "assumptions",
        "method",
    }
    assert set(schema["risks"][0]) == {"risk", "likelihood", "mitigation"}
    assert set(schema["recommendations"][0]) == {
        "action",
        "horizon",
        "priority",
        "expected_impact",
        "est_roi",
    }


def test_report_skill_is_compact_and_keeps_grounding_guards() -> None:
    prompt = build_report_prompt({"sentinel": 123})
    persona = prompt.split("## Analysis payload (JSON)", 1)[0]
    assert len(persona.split()) < 2_000
    assert "Every cited figure, metric, segment, and period must appear in the payload" in persona
    assert "Correlation does not establish causation" in persona
    assert "Give numerical `est_roi` only when both cost and benefit inputs are supplied" in persona
    assert '"sentinel": 123' in prompt


def test_extract_report_json_prefers_report_among_banners() -> None:
    # codex-style: session/tokens banners around the real answer.
    text = f'{{"session_id": "abc"}}\n{json.dumps(_GOOD)}\n{{"tokens_used": 812}}\n'
    obj = _extract_report_json(text)
    assert obj is not None
    assert obj["executive_summary"].startswith("Revenue is concentrated")


def test_validate_report_happy_path_sets_backend() -> None:
    report = _validate_report(dict(_GOOD), "codex")
    assert report is not None
    assert report.generated_by == "codex"
    assert report.key_insights[0].title == "Concentration"


def test_validate_report_empty_is_none() -> None:
    assert _validate_report({"executive_summary": "", "key_insights": []}, "codex") is None


def test_validate_report_drops_malformed_list_but_keeps_summary() -> None:
    # key_insights arrives as a string (malformed) — it is dropped, summary keeps the report alive.
    report = _validate_report(
        {"executive_summary": "Solid quarter.", "key_insights": "oops"}, "codex"
    )
    assert report is not None
    assert report.key_insights == []
    assert report.executive_summary == "Solid quarter."


def test_resolve_report_with_fake_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "_run", lambda backend, prompt, timeout: json.dumps(_GOOD))
    report = resolve_report({"metric": {"name": "total_amount"}}, Backend("codex", ["codex"]))
    assert report is not None
    assert report.confidence_overall == "high"


def test_resolve_report_run_failure_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "_run", lambda backend, prompt, timeout: None)
    assert resolve_report({}, Backend("codex", ["codex"])) is None


def test_resolve_report_no_json_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "_run", lambda backend, prompt, timeout: "I couldn't help with that.")
    assert resolve_report({}, Backend("codex", ["codex"])) is None
