"""Delegate Claude/Codex authentication to their installed local clients."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from dataclasses import asdict, dataclass
from typing import Any, Literal
from uuid import uuid4

ProviderName = Literal["claude", "codex"]

_STATUS_COMMANDS: dict[ProviderName, list[str]] = {
    "claude": ["claude", "auth", "status", "--json"],
    "codex": ["codex", "login", "status"],
}
_LOGIN_COMMANDS: dict[ProviderName, list[str]] = {
    "claude": ["claude", "auth", "login", "--claudeai"],
    "codex": ["codex", "login"],
}


@dataclass(frozen=True)
class ProviderStatus:
    provider: ProviderName
    installed: bool
    authenticated: bool
    detail: str


class ProviderAuthService:
    """Check and start provider-owned login flows without handling credentials."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}

    def status(self, provider: ProviderName) -> ProviderStatus:
        if shutil.which(provider) is None:
            return ProviderStatus(provider, False, False, f"{provider.title()} is not installed.")
        try:
            result = subprocess.run(  # noqa: S603 - fixed trusted local CLI argv
                _STATUS_COMMANDS[provider],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ProviderStatus(provider, True, False, "Authentication status unavailable.")
        authenticated = self._authenticated(provider, result)
        detail = (
            "Connected"
            if authenticated
            else f"Sign in required. {provider.title()} owns the browser login and credentials."
        )
        return ProviderStatus(provider, True, authenticated, detail)

    def begin_login(self, provider: ProviderName) -> str:
        job_id = "auth_" + uuid4().hex[:12]
        job: dict[str, Any] = {
            "id": job_id,
            "provider": provider,
            "status": "running",
            "message": f"Waiting for {provider.title()} sign-in in your browser…",
            "authenticated": False,
        }
        self.jobs[job_id] = job

        def work() -> None:
            if shutil.which(provider) is None:
                job.update(status="failed", message=f"{provider.title()} is not installed.")
                return
            try:
                result = subprocess.run(  # noqa: S603 - fixed trusted local CLI argv
                    _LOGIN_COMMANDS[provider],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    timeout=600,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                job.update(status="failed", message="Sign-in timed out. Try again.")
                return
            except OSError:
                job.update(status="failed", message="Unable to start the provider login.")
                return

            status = self.status(provider)
            if result.returncode == 0 and status.authenticated:
                job.update(status="completed", message="Connected", authenticated=True)
            else:
                job.update(status="failed", message="Sign-in was not completed.")

        threading.Thread(target=work, daemon=True, name=job_id).start()
        return job_id

    def job(self, job_id: str) -> dict[str, Any] | None:
        job = self.jobs.get(job_id)
        return dict(job) if job else None

    @staticmethod
    def _authenticated(provider: ProviderName, result: subprocess.CompletedProcess[str]) -> bool:
        if result.returncode != 0:
            return False
        if provider == "codex":
            return "logged in" in (result.stdout + result.stderr).casefold()
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            return False
        return payload.get("loggedIn") is True

    def public_status(self, provider: ProviderName) -> dict[str, object]:
        return asdict(self.status(provider))
