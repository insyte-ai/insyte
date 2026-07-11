"""Connector interface and shared result models.

Version 0.1.0 ships a single PostgreSQL connector, but the interface is defined separately so
future engines can implement it without touching callers. Result models are plain dataclasses
carrying only non-secret metadata — never a connection URL or password.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from dataclasses import dataclass, field

from sqlalchemy.engine import Connection

# Privilege types that indicate the account can modify data.
WRITE_PRIVILEGES: tuple[str, ...] = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")


@dataclass
class ServerInfo:
    """Basic identity of the connected server."""

    version: str
    is_postgres: bool
    database: str
    user: str


@dataclass
class SSLInfo:
    """Whether the current connection is encrypted."""

    in_use: bool
    cipher: str | None = None
    protocol: str | None = None


@dataclass
class PermissionReport:
    """What the connected role is allowed to do."""

    is_superuser: bool
    can_create_db: bool
    can_create_role: bool
    has_write_privileges: bool
    write_samples: list[str] = field(default_factory=list)

    @property
    def has_write_access(self) -> bool:
        """True if the role can modify data in any way (superuser or explicit grants)."""

        return self.is_superuser or self.has_write_privileges


@dataclass
class ConnectionCheckResult:
    """Aggregated outcome of a connection health check."""

    server: ServerInfo
    ssl: SSLInfo
    permissions: PermissionReport
    read_only_enforced: bool
    statement_timeout_seconds: int
    lock_timeout_seconds: int
    warnings: list[str] = field(default_factory=list)


class DatabaseConnector(ABC):
    """Abstract database connector.

    Implementations must guarantee that every query runs inside a read-only transaction with
    timeouts applied, and must never expose the connection URL or credentials.
    """

    @property
    @abstractmethod
    def host(self) -> str | None:
        """Non-secret host, for diagnostics."""

    @property
    @abstractmethod
    def port(self) -> int | None:
        """Non-secret port, for diagnostics."""

    @abstractmethod
    def check_connection(self) -> ConnectionCheckResult:
        """Open a read-only transaction, validate the server, and report its capabilities."""

    @abstractmethod
    def read_only_transaction(self) -> AbstractContextManager[Connection]:
        """Yield a connection inside a read-only, timeout-bounded transaction."""

    @abstractmethod
    def dispose(self) -> None:
        """Release any pooled connections."""
