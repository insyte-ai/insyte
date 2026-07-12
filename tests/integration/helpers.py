from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError

from insyte.connectors.postgres import normalize_postgres_url


def load_ecommerce_fixture(database_url: str, fixture: Path) -> None:
    """Load the integration fixture, or skip when the test DB user is read-only."""

    engine = create_engine(normalize_postgres_url(database_url))
    try:
        with engine.begin() as conn:
            for statement in fixture.read_text().split(";\n"):
                if statement.strip():
                    conn.execute(text(statement))
    except ProgrammingError as exc:
        message = str(exc).lower()
        if (
            "insufficientprivilege" in message
            or "must be owner" in message
            or "permission denied" in message
        ):
            pytest.skip(
                "INSYTE_TEST_DATABASE_URL points to a read-only/non-owner database user; "
                "fixture-reset integration tests require a writable throwaway test database."
            )
        raise
    finally:
        engine.dispose()
