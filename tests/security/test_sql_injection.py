"""Security tests: every dangerous query from the spec (§23) must be rejected.

These assert the validator refuses the query — no database is involved, so the checks are
hermetic and fast.
"""

from __future__ import annotations

import pytest

from insyte.config.models import DatabaseSection, InsyteConfig, ProjectSection
from insyte.query.validator import ValidationContext, validate_query


@pytest.fixture
def context() -> ValidationContext:
    config = InsyteConfig(
        project=ProjectSection(name="sec"),
        database=DatabaseSection(
            allowed_schemas=["public"],
            blocked_tables=["audit_log"],
            blocked_columns=["users.password_hash", "users.auth_token"],
        ),
    )
    return ValidationContext.from_config(config)


DANGEROUS = [
    "DELETE FROM orders",
    "DROP TABLE customers",
    "WITH deleted AS (DELETE FROM orders RETURNING *) SELECT * FROM deleted",
    "SELECT * FROM users",
    "SELECT password_hash FROM users",
    "SELECT pg_sleep(120)",
    "SELECT * FROM orders CROSS JOIN customers CROSS JOIN products",
    "SELECT 1; DROP TABLE orders",
    "TRUNCATE orders",
    "UPDATE orders SET total_amount = 0",
    "INSERT INTO orders (id) VALUES (1)",
    "ALTER TABLE orders ADD COLUMN x int",
    "CREATE TABLE evil (id int)",
    "GRANT ALL ON orders TO public",
    "REVOKE ALL ON orders FROM public",
    "SELECT * FROM pg_catalog.pg_user",
    "SELECT * FROM audit_log",
    "SELECT users.auth_token FROM users",
    "COPY orders TO '/tmp/x.csv'",
    "SELECT pg_read_file('/etc/passwd')",
    "WITH x AS (UPDATE orders SET total_amount = 1 RETURNING *) SELECT * FROM x",
    "DO $$ BEGIN PERFORM 1; END $$",
]


@pytest.mark.parametrize("sql", DANGEROUS)
def test_dangerous_query_is_rejected(sql: str, context: ValidationContext) -> None:
    result = validate_query(sql, context)
    assert result.valid is False, f"should have been blocked: {sql}"
    assert result.violations
    assert result.normalized_sql is None  # nothing to execute


SAFE = [
    "SELECT city, sum(total_amount) FROM public.orders GROUP BY 1",
    "SELECT * FROM orders o JOIN customers c ON o.customer_id = c.id",
    "SELECT count(*) FROM orders WHERE status = 'completed'",
    "WITH monthly AS (SELECT date_trunc('month', created_at) m FROM orders) SELECT * FROM monthly",
    "SELECT email FROM customers",
]


@pytest.mark.parametrize("sql", SAFE)
def test_safe_query_is_allowed(sql: str, context: ValidationContext) -> None:
    result = validate_query(sql, context)
    assert result.valid is True, f"should have been allowed: {sql} ({result.violations})"
    assert result.normalized_sql is not None
    assert "LIMIT" in result.normalized_sql.upper()  # a bound is always applied
