"""SQL safety validator built on the SQLGlot syntax tree.

The validator never uses string matching to decide what a query does — it parses the SQL and
inspects the AST. A query is accepted only if it is a single read-only statement that touches
allowed schemas/tables/columns, avoids unsafe functions and excessive cartesian joins, and
carries a bounded row limit (added automatically when missing).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from insyte.config.models import InsyteConfig
from insyte.query.cost_guard import (
    DEFAULT_MAX_CARTESIAN_TABLES,
    apply_row_limit,
    max_cartesian_width,
)
from insyte.query.models import QueryValidationResult

_DIALECT = "postgres"

# Statement types that write or change structure — forbidden anywhere in the tree (including
# inside CTEs). Resolved by name so the set works across SQLGlot versions.
_FORBIDDEN_TYPE_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Drop",
    "Create",
    "Alter",
    "TruncateTable",
    "Grant",
    "Revoke",
    "Command",  # SQLGlot's catch-all for statements it can't fully parse (GRANT, DO, CALL, …)
    "Set",
    "Copy",
    "Transaction",
    "Commit",
    "Rollback",
)
_FORBIDDEN_TYPES = tuple(
    cls for name in _FORBIDDEN_TYPE_NAMES if (cls := getattr(exp, name, None)) is not None
)

_ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)

# Functions that read files, sleep, run code, or otherwise escape a read-only analytical scope.
_UNSAFE_FUNCTIONS = {
    "pg_sleep",
    "pg_sleep_for",
    "pg_sleep_until",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "lo_import",
    "lo_export",
    "lo_get",
    "lo_put",
    "dblink",
    "dblink_exec",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "pg_reload_conf",
    "set_config",
    "copy_from",
    "query_to_xml",
    "pg_read_server_files",
}

_SYSTEM_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast"}


@dataclass
class ValidationContext:
    """Everything the validator needs to judge a query, derived from project config."""

    allowed_schemas: set[str]
    blocked_tables: set[str]
    blocked_columns: set[str]  # "table.column"
    blocked_column_tables: set[str]  # tables that own at least one blocked column
    blocked_column_names: set[str]  # bare blocked column names
    default_limit: int
    maximum_limit: int
    max_cartesian_tables: int = DEFAULT_MAX_CARTESIAN_TABLES

    @classmethod
    def from_config(cls, config: InsyteConfig) -> ValidationContext:
        db = config.database
        blocked_columns: set[str] = set()
        blocked_column_tables: set[str] = set()
        blocked_column_names: set[str] = set()
        for item in db.blocked_columns:
            parts = item.lower().split(".")
            if len(parts) >= 2:
                table, column = parts[-2], parts[-1]
                blocked_columns.add(f"{table}.{column}")
                blocked_column_tables.add(table)
                blocked_column_names.add(column)
        return cls(
            allowed_schemas={s.lower() for s in db.allowed_schemas},
            blocked_tables={t.lower() for t in db.blocked_tables},
            blocked_columns=blocked_columns,
            blocked_column_tables=blocked_column_tables,
            blocked_column_names=blocked_column_names,
            default_limit=config.query.default_limit,
            maximum_limit=config.query.maximum_limit,
        )


def validate_query(sql: str, context: ValidationContext) -> QueryValidationResult:
    """Validate ``sql`` and return the result, including a normalized, limited SQL string."""

    statements = _parse(sql)
    if statements is None:
        return _invalid(["The query could not be parsed as valid SQL."])
    if len(statements) != 1:
        return _invalid(["Multiple SQL statements are not allowed; submit a single query."])

    root = statements[0]
    violations: list[str] = []

    if not isinstance(root, _ALLOWED_ROOTS):
        return _invalid([f"Only read-only SELECT queries are allowed (got {root.key.upper()})."])

    violations.extend(_check_forbidden_nodes(root))
    violations.extend(_check_unsafe_functions(root))

    cte_names = {c.alias_or_name.lower() for c in root.find_all(exp.CTE)}
    referenced_tables = _referenced_tables(root, cte_names)
    referenced_columns = _referenced_columns(root)

    violations.extend(_check_schemas_and_tables(root, context, cte_names))
    violations.extend(_check_columns(root, context))
    violations.extend(_check_cartesian(root, context))

    if violations:
        return QueryValidationResult(
            valid=False,
            normalized_sql=None,
            violations=violations,
            referenced_tables=referenced_tables,
            referenced_columns=referenced_columns,
            applied_limit=None,
        )

    limited, applied_limit = apply_row_limit(
        cast(exp.Query, root), context.default_limit, context.maximum_limit
    )
    return QueryValidationResult(
        valid=True,
        normalized_sql=limited.sql(dialect=_DIALECT),
        violations=[],
        referenced_tables=referenced_tables,
        referenced_columns=referenced_columns,
        applied_limit=applied_limit,
    )


def _parse(sql: str) -> list[exp.Expression] | None:
    try:
        parsed = sqlglot.parse(sql, read=_DIALECT)
    except SqlglotError:
        return None
    statements = [s for s in parsed if s is not None]
    if not statements:
        return None
    return cast("list[exp.Expression]", statements)


def _check_forbidden_nodes(root: exp.Expression) -> list[str]:
    for node in root.walk():
        if isinstance(node, _FORBIDDEN_TYPES):
            return [f"Statement type '{node.key.upper()}' is not allowed in a read-only query."]
    return []


def _check_unsafe_functions(root: exp.Expression) -> list[str]:
    # Anonymous (e.g. pg_sleep) is a subclass of Func, so this catches unmapped functions too.
    violations: list[str] = []
    for func in root.find_all(exp.Func):
        name = _function_name(func)
        if name and name.lower() in _UNSAFE_FUNCTIONS:
            violations.append(f"Use of unsafe function '{name}' is not allowed.")
    return _dedupe(violations)


def _function_name(func: exp.Func) -> str | None:
    if isinstance(func, exp.Anonymous):
        this = func.this
        return this if isinstance(this, str) else None
    return func.sql_name()


def _check_schemas_and_tables(
    root: exp.Expression, context: ValidationContext, cte_names: set[str]
) -> list[str]:
    violations: list[str] = []
    for table in root.find_all(exp.Table):
        name = table.name.lower()
        schema = (table.db or "").lower()
        if not schema and name in cte_names:
            continue  # reference to a CTE, not a real table
        if schema and schema not in context.allowed_schemas:
            violations.append(f"Access to schema '{schema}' is not allowed.")
        elif schema in _SYSTEM_SCHEMAS:
            violations.append(f"Access to system schema '{schema}' is not allowed.")
        qualified = f"{schema}.{name}" if schema else name
        if name in context.blocked_tables or qualified in context.blocked_tables:
            violations.append(f"Access to blocked table '{qualified}' is not allowed.")
    return _dedupe(violations)


def _check_columns(root: exp.Expression, context: ValidationContext) -> list[str]:
    if not context.blocked_columns:
        return []

    violations: list[str] = []
    referenced = {t.lower() for t in _table_names(root)}

    for column in root.find_all(exp.Column):
        table = (column.table or "").lower()
        name = column.name.lower()
        if table and f"{table}.{name}" in context.blocked_columns:
            violations.append(f"Access to blocked column '{table}.{name}' is not allowed.")
        elif (
            not table
            and name in context.blocked_column_names
            and (referenced & context.blocked_column_tables)
        ):
            owner = next(iter(referenced & context.blocked_column_tables))
            violations.append(f"Access to blocked column '{owner}.{name}' is not allowed.")

    # SELECT * (or t.*) over a table with blocked columns cannot be masked — reject it.
    if any(isinstance(s, exp.Star) for s in root.find_all(exp.Star)):
        exposed = referenced & context.blocked_column_tables
        for owner in sorted(exposed):
            violations.append(
                f"'SELECT *' over table '{owner}' may expose a blocked column; "
                "list columns explicitly."
            )
    return _dedupe(violations)


def _check_cartesian(root: exp.Expression, context: ValidationContext) -> list[str]:
    width = max_cartesian_width(root)
    if width > context.max_cartesian_tables:
        return [
            f"Excessive cross join across {width} tables is not allowed "
            f"(limit {context.max_cartesian_tables})."
        ]
    return []


def _referenced_tables(root: exp.Expression, cte_names: set[str]) -> list[str]:
    names: set[str] = set()
    for table in root.find_all(exp.Table):
        schema = (table.db or "").lower()
        name = table.name.lower()
        if not schema and name in cte_names:
            continue
        names.add(f"{schema}.{name}" if schema else name)
    return sorted(names)


def _table_names(root: exp.Expression) -> set[str]:
    return {table.name for table in root.find_all(exp.Table)}


def _referenced_columns(root: exp.Expression) -> list[str]:
    names: set[str] = set()
    for column in root.find_all(exp.Column):
        table = column.table
        names.add(f"{table}.{column.name}" if table else column.name)
    return sorted(names)


def _invalid(violations: list[str]) -> QueryValidationResult:
    return QueryValidationResult(
        valid=False,
        normalized_sql=None,
        violations=violations,
        referenced_tables=[],
        referenced_columns=[],
        applied_limit=None,
    )


def _dedupe(items: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen)
