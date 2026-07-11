"""Custom exception hierarchy for Insyte.

Every error Insyte raises deliberately inherits from :class:`InsyteError` so callers
(the CLI, the future MCP server) can distinguish expected, user-facing failures from
unexpected bugs.
"""

from __future__ import annotations


class InsyteError(Exception):
    """Base class for all Insyte errors."""


class ConfigError(InsyteError):
    """Raised when configuration is invalid or cannot be loaded."""


class InvalidProjectNameError(InsyteError):
    """Raised when a project name is unsafe or malformed."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(
            f"Invalid project name {name!r}. Use letters, digits, '.', '_' or '-' "
            "(1-64 characters, not starting with a separator)."
        )


class ProjectNotFoundError(InsyteError):
    """Raised when a requested project does not exist on disk."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Project {name!r} was not found under the Insyte home directory.")


class ProjectExistsError(InsyteError):
    """Raised when creating a project that already exists."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Project {name!r} already exists. Use --force to overwrite it.")


class SecretResolutionError(InsyteError):
    """Raised when a database URL or other secret cannot be resolved.

    The message never contains the secret value itself.
    """


class DatabaseConnectionError(InsyteError):
    """Raised when Insyte cannot connect to the database.

    Carries the (non-secret) host and port so the CLI can render a helpful message without
    ever touching the password.
    """

    def __init__(self, host: str | None, port: int | None, reason: str | None = None) -> None:
        self.host = host
        self.port = port
        self.reason = reason
        target = f"{host or 'unknown host'}:{port or 5432}"
        message = f"Unable to connect to the database at {target}."
        if reason:
            message = f"{message} {reason}"
        super().__init__(message)


class UnsupportedDatabaseError(InsyteError):
    """Raised when the configured database is not a supported engine."""


class QueryValidationError(InsyteError):
    """Raised when a query fails safety validation. Carries the specific violations.

    No query is sent to the database when this is raised.
    """

    def __init__(self, violations: list[str]) -> None:
        self.violations = violations
        joined = "; ".join(violations) if violations else "query rejected"
        super().__init__(f"Query blocked by Insyte: {joined}")


class QueryExecutionError(InsyteError):
    """Raised when a validated query fails during execution (timeout, missing table, …)."""


class MetricNotFoundError(InsyteError):
    """Raised when a requested metric is not defined in the semantic layer."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Metric {name!r} is not defined. See 'insyte metrics'.")


class DimensionNotFoundError(InsyteError):
    """Raised when a requested dimension is not defined in the semantic layer."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Dimension {name!r} is not defined. See 'insyte metrics'.")


class JoinPathError(InsyteError):
    """Raised when no join path can be found between a metric and a dimension."""


class AnalysisError(InsyteError):
    """Raised for general analysis failures (bad grain, unusable metric, …)."""
