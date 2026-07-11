"""Console entry point for the ``insyte`` command."""

from __future__ import annotations

from insyte.cli.app import app


def main() -> None:
    """Run the Insyte CLI."""

    app()


if __name__ == "__main__":
    main()
