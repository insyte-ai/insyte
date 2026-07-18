"""Check PyPI for a newer Insyte release without changing local installation state."""

from __future__ import annotations

import json
import ssl
from dataclasses import dataclass
from urllib.error import URLError
from urllib.request import urlopen

import certifi
from packaging.version import InvalidVersion, Version

from insyte import __version__

PYPI_JSON_URL = "https://pypi.org/pypi/insyte/json"
RELEASES_URL = "https://github.com/insyte-ai/insyte/releases/latest"


@dataclass(frozen=True)
class UpdateStatus:
    current_version: str
    latest_version: str | None
    update_available: bool
    release_url: str
    error: str | None = None


class UpdateService:
    """Read public release metadata; never install or mutate packages."""

    def __init__(self, endpoint: str = PYPI_JSON_URL) -> None:
        self._endpoint = endpoint

    def check(self, *, timeout: float = 4.0) -> UpdateStatus:
        try:
            context = ssl.create_default_context(cafile=certifi.where())
            with urlopen(self._endpoint, timeout=timeout, context=context) as response:  # noqa: S310
                payload = json.load(response)
            latest = str(payload["info"]["version"])
            available = Version(latest) > Version(__version__)
            return UpdateStatus(__version__, latest, available, RELEASES_URL)
        except (OSError, URLError, KeyError, TypeError, ValueError, InvalidVersion):
            return UpdateStatus(
                __version__,
                None,
                False,
                RELEASES_URL,
                "Could not securely connect to the update server. "
                "Check your internet connection and try again.",
            )
