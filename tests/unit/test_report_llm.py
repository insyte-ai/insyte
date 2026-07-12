"""Unit tests for the detailed-report LLM path (nl/llm.py) — no real CLI is ever spawned."""

from __future__ import annotations

import json

import pytest

from insyte.nl import llm
from insyte.nl.llm import (
    Backend,
    _extract_report_json,
    _validate_report,
    resolve_report,
)

_GOOD = {
    "executive_summary": "Revenue is concentrated in Mumbai.",
    "key_insights": [
        {"title": "Concentration", "detail": "Top city is 70%.", "confidence": "high"}
    ],
    "recommendations": [{"action": "Diversify", "horizon": "short", "priority": "high"}],
    "confidence_overall": "high",
}


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
