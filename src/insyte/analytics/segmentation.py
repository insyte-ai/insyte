"""Segmentation helpers: rank a metric's breakdown by contribution."""

from __future__ import annotations

from insyte.analytics.models import Contributor


def rank_contributors(rows: list[tuple[object, ...]]) -> list[Contributor]:
    """Turn ``(segment, value)`` rows into contributors ranked by absolute value.

    ``share`` is each segment's fraction of the total of positive values.
    """

    pairs: list[tuple[str, float]] = []
    for row in rows:
        if len(row) < 2:
            continue
        value = _as_float(row[1])
        if value is None:
            continue
        pairs.append((_as_str(row[0]), value))

    total = sum(v for _, v in pairs if v > 0)
    contributors = [
        Contributor(segment=segment, value=value, share=(value / total if total else 0.0))
        for segment, value in pairs
    ]
    contributors.sort(key=lambda c: abs(c.value), reverse=True)
    return contributors


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _as_str(value: object) -> str:
    return "—" if value is None else str(value)
