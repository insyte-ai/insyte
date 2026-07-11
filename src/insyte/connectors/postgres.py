"""PostgreSQL connector built on SQLAlchemy 2.x and psycopg 3.

Safety model (spec §8): every connection uses a small, non-overflowing pool with pre-ping and
a connect timeout; every transaction is explicitly set ``READ ONLY`` with statement, lock and
idle timeouts applied before any query runs. The connection URL never leaves this module.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Connection, Engine, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from insyte.config.models import DatabaseSection, QuerySection
from insyte.connectors.base import (
    WRITE_PRIVILEGES,
    ConnectionCheckResult,
    DatabaseConnector,
    PermissionReport,
    ServerInfo,
    SSLInfo,
)
from insyte.exceptions import DatabaseConnectionError, UnsupportedDatabaseError
from insyte.logging_config import get_logger

logger = get_logger("connectors.postgres")

CONNECT_TIMEOUT_SECONDS = 10
IDLE_IN_TRANSACTION_TIMEOUT_SECONDS = 30
POOL_SIZE = 2
APPLICATION_NAME = "insyte"

_PG_BACKENDS = {"postgresql", "postgres"}

# Catalog query: privileges on user tables that would allow writes. System schemas are
# excluded — e.g. pg_catalog.pg_settings carries an UPDATE grant to PUBLIC (for SET) that does
# not represent real write capability, and would otherwise flag read-only roles as writers.
_WRITE_PRIVILEGE_SQL = text(
    """
    SELECT table_schema, table_name, privilege_type
    FROM information_schema.table_privileges
    WHERE grantee IN (current_user, 'PUBLIC')
      AND privilege_type = ANY(:write_privileges)
      AND table_schema NOT IN ('pg_catalog', 'information_schema')
    ORDER BY table_schema, table_name
    LIMIT 20
    """
)


def normalize_postgres_url(raw: str) -> URL:
    """Parse a database URL and force the psycopg 3 driver.

    Raises :class:`UnsupportedDatabaseError` for non-PostgreSQL URLs and
    :class:`DatabaseConnectionError` for malformed ones. Never logs the URL.
    """

    try:
        url = make_url(raw)
    except ArgumentError as exc:
        raise DatabaseConnectionError(None, None, "The database URL is malformed.") from exc

    backend = url.get_backend_name()
    if backend not in _PG_BACKENDS:
        raise UnsupportedDatabaseError(
            f"Database backend {backend!r} is not supported. Insyte 0.1.0 supports PostgreSQL."
        )
    return url.set(drivername="postgresql+psycopg")


def build_connect_args(database: DatabaseSection, url: URL) -> dict[str, Any]:
    """Build psycopg connect arguments: timeout, application name, and SSL mode.

    The ``sslmode`` from config is only applied when the URL does not already specify one, so
    an explicit URL parameter always wins.
    """

    args: dict[str, Any] = {
        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
        "application_name": APPLICATION_NAME,
    }
    if "sslmode" not in url.query:
        args["sslmode"] = database.ssl_mode.value
    return args


def build_engine(url: URL, connect_args: dict[str, Any]) -> Engine:
    """Create a SQLAlchemy engine with a small, pre-pinged, non-overflowing pool."""

    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=POOL_SIZE,
        max_overflow=0,
        connect_args=connect_args,
    )


def read_only_setup_statements(query: QuerySection) -> list[str]:
    """Return the ``SET`` statements that make a transaction safe, in required order.

    ``SET TRANSACTION READ ONLY`` must run first, before any other statement in the
    transaction. Values come from validated integer config, so string interpolation is safe.
    """

    return [
        "SET TRANSACTION READ ONLY",
        f"SET LOCAL statement_timeout = '{query.timeout_seconds}s'",
        f"SET LOCAL lock_timeout = '{query.lock_timeout_seconds}s'",
        f"SET LOCAL idle_in_transaction_session_timeout = '{IDLE_IN_TRANSACTION_TIMEOUT_SECONDS}s'",
    ]


def interpret_permissions(
    role_row: tuple[bool, bool, bool] | None,
    write_rows: list[tuple[str, str, str]],
) -> PermissionReport:
    """Turn raw catalog rows into a :class:`PermissionReport`."""

    is_super, can_create_db, can_create_role = role_row or (False, False, False)
    samples = [f"{schema}.{table} ({priv})" for schema, table, priv in write_rows]
    return PermissionReport(
        is_superuser=bool(is_super),
        can_create_db=bool(can_create_db),
        can_create_role=bool(can_create_role),
        has_write_privileges=bool(write_rows),
        write_samples=samples,
    )


class PostgresConnector(DatabaseConnector):
    """Read-only PostgreSQL connector."""

    def __init__(
        self,
        database_url: str,
        database_config: DatabaseSection,
        query_config: QuerySection,
    ) -> None:
        self._url = normalize_postgres_url(database_url)
        self._database_config = database_config
        self._query_config = query_config
        self._engine: Engine | None = None

    @property
    def host(self) -> str | None:
        return self._url.host

    @property
    def port(self) -> int | None:
        return self._url.port

    def _engine_or_create(self) -> Engine:
        if self._engine is None:
            connect_args = build_connect_args(self._database_config, self._url)
            self._engine = build_engine(self._url, connect_args)
        return self._engine

    @contextmanager
    def read_only_transaction(self) -> Iterator[Connection]:
        """Yield a connection inside a read-only, timeout-bounded transaction.

        The transaction is always rolled back on exit — Insyte never commits.
        """

        engine = self._engine_or_create()
        try:
            connection = engine.connect()
        except SQLAlchemyError as exc:
            raise DatabaseConnectionError(self.host, self.port, _hint(exc)) from exc

        transaction = connection.begin()
        try:
            for statement in read_only_setup_statements(self._query_config):
                connection.execute(text(statement))
            yield connection
        finally:
            transaction.rollback()
            connection.close()

    def check_connection(self) -> ConnectionCheckResult:
        """Validate the connection and report server info, SSL, and permissions."""

        logger.info("connection_check_started", extra={"host": self.host, "port": self.port})
        try:
            with self.read_only_transaction() as conn:
                conn.execute(text("SELECT 1"))
                version = str(conn.execute(text("SELECT version()")).scalar_one())
                database = str(conn.execute(text("SELECT current_database()")).scalar_one())
                user = str(conn.execute(text("SELECT current_user")).scalar_one())
                ssl_row = conn.execute(
                    text(
                        "SELECT ssl, cipher, version FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
                    )
                ).one_or_none()
                role_row = conn.execute(
                    text(
                        "SELECT rolsuper, rolcreatedb, rolcreaterole "
                        "FROM pg_roles WHERE rolname = current_user"
                    )
                ).one_or_none()
                write_rows = conn.execute(
                    _WRITE_PRIVILEGE_SQL,
                    {"write_privileges": list(WRITE_PRIVILEGES)},
                ).all()
        except DatabaseConnectionError:
            raise
        except SQLAlchemyError as exc:
            raise DatabaseConnectionError(self.host, self.port, _hint(exc)) from exc

        is_postgres = "postgresql" in version.lower()
        server = ServerInfo(version=version, is_postgres=is_postgres, database=database, user=user)
        ssl = _build_ssl_info(ssl_row)
        permissions = interpret_permissions(
            role_row,  # type: ignore[arg-type]
            [tuple(row) for row in write_rows],
        )
        warnings = _collect_warnings(server, ssl, permissions, self._database_config)

        logger.info(
            "connection_check_ok",
            extra={
                "host": self.host,
                "port": self.port,
                "database": database,
                "is_postgres": is_postgres,
                "ssl": ssl.in_use,
                "has_write_access": permissions.has_write_access,
            },
        )
        return ConnectionCheckResult(
            server=server,
            ssl=ssl,
            permissions=permissions,
            read_only_enforced=True,
            statement_timeout_seconds=self._query_config.timeout_seconds,
            lock_timeout_seconds=self._query_config.lock_timeout_seconds,
            warnings=warnings,
        )

    def dispose(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None


def _build_ssl_info(row: Any) -> SSLInfo:
    if row is None:
        return SSLInfo(in_use=False)
    in_use, cipher, protocol = row
    return SSLInfo(in_use=bool(in_use), cipher=cipher, protocol=protocol)


def _collect_warnings(
    server: ServerInfo,
    ssl: SSLInfo,
    permissions: PermissionReport,
    database: DatabaseSection,
) -> list[str]:
    warnings: list[str] = []
    if not server.is_postgres:
        warnings.append("The server does not identify as PostgreSQL.")
    if permissions.has_write_access:
        warnings.append(
            "This database user has write permissions. Insyte still enforces read-only "
            "transactions, but a dedicated read-only account is strongly recommended."
        )
    if not ssl.in_use and database.ssl_mode.value in {"require", "verify-ca", "verify-full"}:
        warnings.append(f"SSL is not in use although ssl_mode is '{database.ssl_mode.value}'.")
    return warnings


def _hint(exc: SQLAlchemyError) -> str:
    """Return a short, credential-free hint from a SQLAlchemy error."""

    text_value = str(getattr(exc, "orig", exc))
    first_line = (
        text_value.strip().splitlines()[0] if text_value.strip() else exc.__class__.__name__
    )
    return first_line[:200]
