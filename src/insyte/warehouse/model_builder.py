"""Build convenience models (views) over synced tables in DuckDB.

Deliberately minimal: it creates a plain unqualified view per synced table so queries can
reference ``orders`` as well as ``public.orders``. It does not build a giant denormalised flat
table (explicitly out of scope for 0.1.0).
"""

from __future__ import annotations

from insyte.metadata.models import SyncState
from insyte.warehouse.duckdb_manager import DuckDBManager


def ensure_models(duckdb: DuckDBManager, states: list[SyncState]) -> None:
    """Ensure a convenience view exists for each synced table."""

    for state in states:
        schema, _, table = state.table.rpartition(".")
        if schema and table:
            duckdb.create_convenience_view(schema, table)
