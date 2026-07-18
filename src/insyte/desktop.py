"""Graphical desktop entry point: start local Studio and open the browser."""

from __future__ import annotations

import multiprocessing
import os

from insyte.cli.studio_command import DEFAULT_HOST, DEFAULT_PORT, studio


def main() -> None:
    """Launch browser-first Studio without requiring CLI arguments."""

    multiprocessing.freeze_support()
    if os.environ.get("INSYTE_DESKTOP_VALIDATE_BUNDLE") == "1":
        from pathlib import Path

        import certifi
        from sqlglot import parse_one

        parse_one("SELECT 1").sql(dialect="postgres")
        if not Path(certifi.where()).is_file():
            raise RuntimeError("Bundled CA certificate file is missing")
        return
    port = int(os.environ.get("INSYTE_DESKTOP_PORT", DEFAULT_PORT))
    studio(
        project=None,
        host=DEFAULT_HOST,
        port=port,
        no_browser=os.environ.get("INSYTE_DESKTOP_NO_BROWSER") == "1",
        reload=False,
    )


if __name__ == "__main__":
    main()
