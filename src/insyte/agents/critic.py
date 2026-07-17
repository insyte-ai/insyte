"""Grounding critic for structured reports."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

from insyte.agents.schemas import CriticReview
from insyte.studio.schemas import DetailedReport

_NUMBER = re.compile(r"(?<![\w])[-+]?\d[\d,]*(?:\.\d+)?%?")


def _normalized_numbers(value: object) -> set[str]:
    raw = json.dumps(value, default=str, ensure_ascii=False)
    return {_normalize(token) for token in _NUMBER.findall(raw)}


def _normalize(token: str) -> str:
    token = token.replace(",", "").removesuffix("%")
    try:
        return f"{float(token):.12g}"
    except ValueError:
        return token


def _report_strings(value: object, path: str = "report") -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _report_strings(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            if key != "generated_by":
                yield from _report_strings(item, f"{path}.{key}")


class CriticAgent:
    """Reject reports that introduce figures absent from the supplied evidence payload."""

    def review(self, report: DetailedReport, evidence: dict) -> CriticReview:
        allowed = _normalized_numbers(evidence)
        unsupported: list[str] = []
        for path, claim in _report_strings(report.model_dump(mode="json")):
            unknown = sorted(
                token
                for token in {_normalize(v) for v in _NUMBER.findall(claim)}
                if token not in allowed
            )
            if unknown:
                unsupported.append(f"{path} introduces unsupported figure(s): {', '.join(unknown)}")
        if unsupported:
            return CriticReview(
                approved=False,
                unsupported_claims=unsupported[:10],
                action="block",
                confidence="low",
            )
        return CriticReview(approved=True)

    def sanitize(self, report: DetailedReport, evidence: dict) -> DetailedReport:
        """Remove only report claims containing figures absent from ``evidence``."""

        allowed = _normalized_numbers(evidence)

        def has_unsupported(value: object) -> bool:
            return isinstance(value, str) and any(
                _normalize(token) not in allowed for token in _NUMBER.findall(value)
            )

        def clean(value: object) -> object:
            if isinstance(value, str):
                return "" if has_unsupported(value) else value
            if isinstance(value, list):
                return [clean(item) for item in value if not _contains(value=item)]
            if isinstance(value, dict):
                return {key: clean(item) for key, item in value.items()}
            return value

        def _contains(value: object) -> bool:
            if has_unsupported(value):
                return True
            if isinstance(value, list):
                return any(_contains(item) for item in value)
            if isinstance(value, dict):
                return any(_contains(item) for item in value.values())
            return False

        data = clean(report.model_dump(mode="json"))
        assert isinstance(data, dict)
        caveats = data.setdefault("caveats", [])
        if isinstance(caveats, list):
            caveats.append("Unsupported numeric claims were removed by grounding review.")
        return DetailedReport.model_validate(data)
