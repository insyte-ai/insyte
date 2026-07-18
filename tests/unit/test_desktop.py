"""Standalone desktop entry-point tests."""

from __future__ import annotations

import pytest

from insyte import desktop


def test_desktop_launches_browser_first_studio(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(desktop.multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setattr(desktop, "studio", lambda **kwargs: calls.append(kwargs))

    desktop.main()

    assert calls == [
        {
            "project": None,
            "host": "127.0.0.1",
            "port": 3838,
            "no_browser": False,
            "reload": False,
        }
    ]


def test_desktop_supports_headless_smoke_test(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    monkeypatch.setenv("INSYTE_DESKTOP_NO_BROWSER", "1")
    monkeypatch.setenv("INSYTE_DESKTOP_PORT", "43838")
    monkeypatch.setattr(desktop.multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setattr(desktop, "studio", lambda **kwargs: calls.append(kwargs))

    desktop.main()

    assert calls[0]["port"] == 43838
    assert calls[0]["no_browser"] is True


def test_desktop_validates_frozen_runtime_without_starting_studio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INSYTE_DESKTOP_VALIDATE_BUNDLE", "1")
    monkeypatch.setattr(desktop.multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setattr(
        desktop,
        "studio",
        lambda **_kwargs: pytest.fail("Studio should not start during bundle validation."),
    )

    desktop.main()
