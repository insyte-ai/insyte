"""Project composition root — builds the shared services for a project.

The TUI, MCP server and Studio API all obtain their services from here, so they share one
connector policy (direct vs local warehouse), one metadata database, and one semantic layer.
Decoupled from Typer: raises :class:`InsyteError` rather than exiting.
"""

from __future__ import annotations

from dataclasses import dataclass

from insyte.analytics.engine import AnalyticsEngine
from insyte.config import loader, paths
from insyte.config.models import InsyteConfig
from insyte.connectors.base import DatabaseConnector
from insyte.connectors.factory import build_analytics_connector
from insyte.exceptions import ProjectNotFoundError
from insyte.metadata.repository import MetadataRepository
from insyte.query.executor import QueryExecutor
from insyte.semantic.repository import SemanticRepository
from insyte.services.analysis_service import AnalysisService
from insyte.services.conversation_service import ConversationService
from insyte.services.history_service import HistoryService
from insyte.services.metric_service import MetricService
from insyte.services.schema_service import SchemaService


def resolve_project_name(project: str | None) -> str:
    """Resolve the target project (explicit, active, or the only one). Raises if none."""

    projects = loader.list_projects()
    if not projects:
        raise ProjectNotFoundError("<none>")
    name = project or paths.get_active_project() or projects[0]
    if name not in projects:
        raise ProjectNotFoundError(name)
    return name


@dataclass
class ProjectServices:
    """The shared services for one open project."""

    config: InsyteConfig
    metadata: MetadataRepository
    semantic: SemanticRepository
    schema: SchemaService
    history: HistoryService
    metrics: MetricService
    conversations: ConversationService

    def build_analysis(self) -> tuple[AnalysisService, DatabaseConnector]:
        """Build an analysis service and its connector (caller disposes the connector)."""

        connector = build_analytics_connector(self.config)
        executor = QueryExecutor(connector, self.config, self.metadata)
        layer = self.semantic.load()
        relationships = self.metadata.list_relationships() if self.metadata.has_metadata() else []
        engine = AnalyticsEngine(executor, layer, relationships)
        return AnalysisService(engine, executor), connector

    def dispose(self) -> None:
        self.metadata.dispose()


class ProjectService:
    """Opens a project and constructs its shared services."""

    @staticmethod
    def open(project: str | None) -> ProjectServices:
        name = resolve_project_name(project)
        config = loader.load_config(name)
        metadata = MetadataRepository(paths.metadata_path(name))
        semantic = SemanticRepository(paths.semantic_path(name))
        return ProjectServices(
            config=config,
            metadata=metadata,
            semantic=semantic,
            schema=SchemaService(metadata),
            history=HistoryService(metadata),
            metrics=MetricService(semantic),
            conversations=ConversationService(metadata, name),
        )
