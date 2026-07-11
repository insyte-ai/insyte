"""Orchestrate syncing a table from PostgreSQL into the local DuckDB."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from insyte.exceptions import InsyteError
from insyte.logging_config import get_logger
from insyte.metadata.models import SyncState
from insyte.metadata.repository import MetadataRepository
from insyte.warehouse.duckdb_manager import DuckDBManager
from insyte.warehouse.extractor import Extractor
from insyte.warehouse.sync_state import detect_cursor

logger = get_logger("warehouse.sync")


@dataclass
class SyncOutcome:
    table: str  # schema.table
    mode: str  # full | incremental
    rows: int
    total_rows: int
    status: str  # completed | failed
    cursor_column: str | None
    last_cursor: str | None
    error: str | None = None


class SyncEngine:
    """Extracts a table to Parquet, loads it into DuckDB, and records sync state."""

    def __init__(
        self,
        metadata: MetadataRepository,
        extractor: Extractor,
        duckdb: DuckDBManager,
    ) -> None:
        self._metadata = metadata
        self._extractor = extractor
        self._duckdb = duckdb

    def sync_table(self, schema: str, table: str, *, incremental: bool) -> SyncOutcome:
        qualified = f"{schema}.{table}"
        started = datetime.now(UTC)
        try:
            outcome = self._run(schema, table, incremental=incremental)
        except InsyteError as exc:
            self._metadata.record_sync_job(
                qualified,
                "incremental" if incremental else "full",
                0,
                "failed",
                started,
                datetime.now(UTC),
                str(exc),
            )
            return SyncOutcome(qualified, "full", 0, 0, "failed", None, None, str(exc))

        self._metadata.record_sync_job(
            qualified, outcome.mode, outcome.rows, "completed", started, datetime.now(UTC)
        )
        return outcome

    def _run(self, schema: str, table: str, *, incremental: bool) -> SyncOutcome:
        qualified = f"{schema}.{table}"
        detail = self._metadata.get_table(schema, table)
        if detail is None:
            raise InsyteError(f"Table '{qualified}' is not in the scanned metadata.")

        columns = [c.name for c in detail.columns]
        cursor_column, cursor_kind = detect_cursor(detail.columns)
        previous = self._metadata.get_sync_state(qualified)

        do_incremental = (
            incremental
            and previous is not None
            and previous.last_cursor is not None
            and cursor_column is not None
        )

        if do_incremental:
            assert previous is not None
            extraction = self._extractor.extract(
                schema,
                table,
                columns,
                cursor_column=cursor_column,
                cursor_kind=cursor_kind,
                last_cursor=previous.last_cursor,
            )
            self._duckdb.load_incremental(schema, table, extraction.parquet_path)
            mode = "incremental"
            last_cursor = extraction.max_cursor or previous.last_cursor
            total_rows = previous.row_count + extraction.row_count
        else:
            extraction = self._extractor.extract(
                schema, table, columns, cursor_column=cursor_column
            )
            self._duckdb.load_full(schema, table, extraction.parquet_path)
            mode = "full"
            last_cursor = extraction.max_cursor
            total_rows = extraction.row_count

        self._duckdb.create_convenience_view(schema, table)
        self._metadata.upsert_sync_state(
            SyncState(
                table=qualified,
                cursor_column=cursor_column,
                cursor_kind=cursor_kind,
                last_cursor=last_cursor,
                status="completed",
                row_count=total_rows,
                mode=mode,
            )
        )
        logger.info(
            "synced", extra={"table": qualified, "mode": mode, "rows": extraction.row_count}
        )
        return SyncOutcome(
            table=qualified,
            mode=mode,
            rows=extraction.row_count,
            total_rows=total_rows,
            status="completed",
            cursor_column=cursor_column,
            last_cursor=last_cursor,
        )
