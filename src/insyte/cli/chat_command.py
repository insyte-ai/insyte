"""``insyte chat`` — launch the interactive terminal analytics UI."""

from __future__ import annotations

import typer
from rich.console import Console

from insyte.analytics.engine import AnalyticsEngine
from insyte.cli._project import resolve_config
from insyte.config import paths
from insyte.config.models import InsyteConfig
from insyte.connectors.base import DatabaseConnector
from insyte.connectors.factory import build_analytics_connector
from insyte.logging_config import configure_logging
from insyte.metadata.repository import MetadataRepository
from insyte.query.executor import QueryExecutor
from insyte.semantic.repository import SemanticRepository
from insyte.tui.app import InsyteApp
from insyte.tui.controller import ChatController

console = Console()


def chat(
    project: str | None = typer.Option(
        None, "--project", "-p", help="Project to open (defaults to the active project)."
    ),
) -> None:
    """Open the interactive terminal analytics interface."""

    config = resolve_config(project)
    configure_logging(log_file=paths.logs_dir(config.project.name) / "insyte.log")

    layer = SemanticRepository(paths.semantic_path(config.project.name)).load()
    metadata = MetadataRepository(paths.metadata_path(config.project.name))

    # The connection is created lazily on the first analysis so the UI still opens (for
    # /schema, /metrics, /help) even when the database is unreachable. In local mode this uses
    # the DuckDB copy and needs no credentials.
    state: dict[str, DatabaseConnector] = {}

    def engine_provider() -> AnalyticsEngine:
        connector = build_analytics_connector(config)
        state["connector"] = connector
        executor = QueryExecutor(connector, config, metadata)
        relationships = metadata.list_relationships() if metadata.has_metadata() else []
        return AnalyticsEngine(executor, layer, relationships)

    controller = ChatController(layer, metadata, engine_provider)
    app = InsyteApp(controller, config.project.name, _status_text(config, metadata))
    try:
        app.run()
    finally:
        if "connector" in state:
            state["connector"].dispose()
        metadata.dispose()


def _status_text(config: InsyteConfig, metadata: MetadataRepository) -> str:
    mode = config.analytics.mode.value
    if metadata.has_metadata():
        latest = metadata.latest_scan()
        table_count = latest.table_count if latest else len(metadata.list_tables())
        scan = "not scanned"
        if latest is not None:
            scan = f"scanned {latest.finished_at.astimezone().strftime('%Y-%m-%d %H:%M')}"
        return f"PostgreSQL · {table_count} tables · {mode} mode · {scan}"
    return f"PostgreSQL · not scanned · {mode} mode · run 'insyte scan'"
