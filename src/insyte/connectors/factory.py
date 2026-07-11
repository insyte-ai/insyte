"""Choose the connector analytical queries should run against.

In ``direct`` mode (or when the local copy is missing) queries hit PostgreSQL. In ``local``
mode, when the DuckDB file exists, queries run against the local copy — no credentials needed.
"""

from __future__ import annotations

from pathlib import Path

from insyte.config import paths
from insyte.config.models import AnalyticsMode, InsyteConfig
from insyte.config.secrets import resolve_database_url
from insyte.connectors.base import DatabaseConnector
from insyte.connectors.duckdb import DuckDBConnector
from insyte.connectors.postgres import PostgresConnector


def duckdb_path(config: InsyteConfig) -> Path:
    """Resolve the project's DuckDB file path."""

    return paths.project_dir(config.project.name) / config.analytics.duckdb_path


def uses_local_warehouse(config: InsyteConfig) -> bool:
    """Whether analytical queries should use the local DuckDB copy."""

    return config.analytics.mode is AnalyticsMode.local and duckdb_path(config).exists()


def build_analytics_connector(config: InsyteConfig) -> DatabaseConnector:
    """Return the connector to run analytical queries against for this project."""

    if uses_local_warehouse(config):
        return DuckDBConnector(duckdb_path(config))
    database_url = resolve_database_url(config.database, config.project.name)
    return PostgresConnector(database_url, config.database, config.query)
