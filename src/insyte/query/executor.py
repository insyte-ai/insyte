"""Safe query executor: validate, execute read-only, and audit — with no bypass.

Every path to the database goes through :meth:`QueryExecutor.execute`. A query is validated
first; a rejected query is recorded as a security event and never reaches the database. A
validated query runs inside the connector's read-only, timeout-bounded transaction and every
attempt (blocked, ok, or error) is written to the audit log.
"""

from __future__ import annotations

import time

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from insyte.config.models import InsyteConfig
from insyte.connectors.base import DatabaseConnector
from insyte.exceptions import QueryExecutionError, QueryValidationError
from insyte.logging_config import get_logger
from insyte.query.models import (
    AuditRecorder,
    ExecutionResult,
    QueryHistoryEntry,
    QueryValidationResult,
    SecurityEventEntry,
)
from insyte.query.validator import ValidationContext, validate_query

logger = get_logger("query.executor")


class QueryExecutor:
    """Validates and executes analytical SQL safely, recording every attempt."""

    def __init__(
        self,
        connector: DatabaseConnector,
        config: InsyteConfig,
        recorder: AuditRecorder,
    ) -> None:
        self._connector = connector
        self._config = config
        self._recorder = recorder
        self._context = ValidationContext.from_config(config)

    def validate(self, sql: str) -> QueryValidationResult:
        """Validate without executing (no audit record)."""

        return validate_query(sql, self._context)

    def execute(self, sql: str, *, source: str = "direct") -> ExecutionResult:
        """Validate, execute (read-only) and audit a query.

        Raises :class:`QueryValidationError` if the query is rejected (nothing is sent to the
        database), or :class:`QueryExecutionError` if a valid query fails at runtime.
        """

        result = validate_query(sql, self._context)
        if not result.valid:
            self._record_blocked(sql, source, result.violations)
            logger.info("query_blocked", extra={"source": source, "violations": result.violations})
            raise QueryValidationError(result.violations)

        assert result.normalized_sql is not None
        try:
            execution = self._run(result)
        except SQLAlchemyError as exc:
            self._record_error(sql, source, result, exc)
            logger.info("query_error", extra={"source": source})
            raise QueryExecutionError(_clean_error(exc)) from exc

        self._record_ok(sql, source, result, execution)
        logger.info(
            "query_ok",
            extra={
                "source": source,
                "rows": execution.row_count,
                "duration_ms": round(execution.duration_ms, 1),
            },
        )
        return execution

    def _run(self, result: QueryValidationResult) -> ExecutionResult:
        assert result.normalized_sql is not None
        max_bytes = self._config.query.maximum_result_bytes
        started = time.perf_counter()
        with self._connector.read_only_transaction() as conn:
            cursor = conn.execute(text(result.normalized_sql))
            columns = list(cursor.keys())
            rows: list[tuple[object, ...]] = []
            truncated = False
            total_bytes = 0
            for row in cursor:
                values = tuple(row)
                total_bytes += _row_bytes(values)
                if total_bytes > max_bytes:
                    truncated = True
                    break
                rows.append(values)
        duration_ms = (time.perf_counter() - started) * 1000
        return ExecutionResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
            duration_ms=duration_ms,
            applied_limit=result.applied_limit,
            normalized_sql=result.normalized_sql,
            referenced_tables=result.referenced_tables,
        )

    def _record_ok(
        self, sql: str, source: str, result: QueryValidationResult, execution: ExecutionResult
    ) -> None:
        self._recorder.record_query(
            QueryHistoryEntry(
                source=source,
                raw_sql=sql,
                normalized_sql=result.normalized_sql,
                referenced_tables=result.referenced_tables,
                status="ok",
                row_count=execution.row_count,
                duration_ms=execution.duration_ms,
                applied_limit=result.applied_limit,
            )
        )

    def _record_error(
        self, sql: str, source: str, result: QueryValidationResult, exc: Exception
    ) -> None:
        self._recorder.record_query(
            QueryHistoryEntry(
                source=source,
                raw_sql=sql,
                normalized_sql=result.normalized_sql,
                referenced_tables=result.referenced_tables,
                status="error",
                applied_limit=result.applied_limit,
                error=_clean_error(exc),
            )
        )

    def _record_blocked(self, sql: str, source: str, violations: list[str]) -> None:
        self._recorder.record_query(
            QueryHistoryEntry(
                source=source,
                raw_sql=sql,
                normalized_sql=None,
                referenced_tables=[],
                status="blocked",
                error="; ".join(violations),
            )
        )
        self._recorder.record_security_event(
            SecurityEventEntry(
                source=source,
                event_type="blocked_query",
                raw_sql=sql,
                violations=violations,
            )
        )


def _row_bytes(values: tuple[object, ...]) -> int:
    return sum(len(str(v)) for v in values) + len(values)


def _clean_error(exc: Exception) -> str:
    text_value = str(getattr(exc, "orig", exc)).strip()
    first_line = text_value.splitlines()[0] if text_value else exc.__class__.__name__
    return first_line[:200]
