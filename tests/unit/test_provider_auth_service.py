"""Provider-owned authentication delegation tests."""

from __future__ import annotations

import subprocess
import time

import pytest

from insyte.services.provider_auth_service import ProviderAuthService


def test_claude_auth_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/claude")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, '{"loggedIn":true,"authMethod":"claude.ai"}', ""
        ),
    )

    status = ProviderAuthService().status("claude")

    assert status.installed is True
    assert status.authenticated is True
    assert status.detail == "Connected"


def test_codex_auth_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/codex")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "Logged in using ChatGPT", ""),
    )

    assert ProviderAuthService().status("codex").authenticated is True


def test_missing_provider_is_not_started(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: None)
    service = ProviderAuthService()

    assert service.status("claude").installed is False
    job_id = service.begin_login("claude")
    for _ in range(50):
        job = service.job(job_id)
        if job and job["status"] != "running":
            break
        time.sleep(0.002)
    assert job is not None
    assert job["status"] == "failed"


def test_login_job_rechecks_provider_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/codex")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "Logged in using ChatGPT", ""),
    )
    service = ProviderAuthService()

    job_id = service.begin_login("codex")
    for _ in range(100):
        job = service.job(job_id)
        if job and job["status"] != "running":
            break
        time.sleep(0.002)

    assert job is not None
    assert job["status"] == "completed"
    assert job["authenticated"] is True
