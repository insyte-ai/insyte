"""Manage the local DuckDB analytical database (loading Parquet extracts)."""

from __future__ import annotations

from pathlib import Path

import duckdb

from insyte.logging_config import get_logger

logger = get_logger("warehouse.duckdb")


class DuckDBManager:
    """Loads Parquet extracts into a DuckDB file, mirroring the source schema.table names."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load_full(self, schema: str, table: str, parquet_path: Path) -> None:
        """Replace a DuckDB table with the contents of a Parquet extract."""

        con = duckdb.connect(str(self._path))
        try:
            con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            con.execute(
                f'CREATE OR REPLACE TABLE "{schema}"."{table}" AS SELECT * FROM read_parquet(?)',
                [str(parquet_path)],
            )
        finally:
            con.close()

    def load_incremental(self, schema: str, table: str, parquet_path: Path) -> None:
        """Append a Parquet delta to an existing DuckDB table (creating it if needed)."""

        con = duckdb.connect(str(self._path))
        try:
            con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            if self._table_exists(con, schema, table):
                con.execute(
                    f'INSERT INTO "{schema}"."{table}" SELECT * FROM read_parquet(?)',
                    [str(parquet_path)],
                )
            else:
                con.execute(
                    f'CREATE TABLE "{schema}"."{table}" AS SELECT * FROM read_parquet(?)',
                    [str(parquet_path)],
                )
        finally:
            con.close()

    def row_count(self, schema: str, table: str) -> int:
        con = duckdb.connect(str(self._path), read_only=True)
        try:
            result = con.execute(f'SELECT count(*) FROM "{schema}"."{table}"').fetchone()
            return int(result[0]) if result else 0
        finally:
            con.close()

    def create_convenience_view(self, schema: str, table: str) -> None:
        """Create an unqualified view so ``table`` resolves without the schema prefix."""

        con = duckdb.connect(str(self._path))
        try:
            con.execute(f'CREATE OR REPLACE VIEW "{table}" AS SELECT * FROM "{schema}"."{table}"')
        finally:
            con.close()

    @staticmethod
    def _table_exists(con: duckdb.DuckDBPyConnection, schema: str, table: str) -> bool:
        result = con.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
            [schema, table],
        ).fetchone()
        return result is not None
