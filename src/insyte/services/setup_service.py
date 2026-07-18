"""Browser- and CLI-independent project onboarding orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from insyte.config import loader, paths
from insyte.config.models import (
    AIClient,
    AISection,
    AnalyticsMode,
    AnalyticsSection,
    DatabaseSection,
    DatabaseType,
    InsyteConfig,
    ProjectSection,
    SSLMode,
)
from insyte.config.secrets import resolve_database_url, store_database_url
from insyte.connectors.base import ConnectionCheckResult, DatabaseConnector
from insyte.connectors.postgres import PostgresConnector
from insyte.metadata.profiler import Profiler
from insyte.metadata.repository import MetadataRepository, utcnow
from insyte.metadata.scanner import SchemaScanner
from insyte.semantic.generator import generate_semantic
from insyte.semantic.repository import SemanticRepository
from insyte.semantic.validator import SchemaIndex, validate_semantic

ProgressCallback = Callable[[str, str], None]
ConnectorFactory = Callable[[str, InsyteConfig], DatabaseConnector]


@dataclass
class SetupOutcome:
    """Summary of a completed deterministic setup run."""

    project: str
    tables: int
    columns: int
    metrics: int
    dimensions: int
    warnings: list[str] = field(default_factory=list)


def _connector(database_url: str, config: InsyteConfig) -> DatabaseConnector:
    return PostgresConnector(database_url, config.database, config.query)


class SetupService:
    """Create and initialise projects without depending on a terminal interface."""

    def __init__(self, connector_factory: ConnectorFactory = _connector) -> None:
        self._connector_factory = connector_factory

    def create_project(
        self,
        *,
        name: str,
        database_url: str,
        schemas: list[str] | None = None,
        ssl_mode: SSLMode = SSLMode.prefer,
        analytics_mode: AnalyticsMode = AnalyticsMode.direct,
        ai_client: AIClient | None = None,
    ) -> tuple[InsyteConfig, ConnectionCheckResult]:
        """Validate a URL, then persist a new project and its local secret."""

        clean_url = database_url.strip()
        config = InsyteConfig(
            project=ProjectSection(name=name),
            database=DatabaseSection(
                type=DatabaseType.postgresql,
                allowed_schemas=schemas or ["public"],
                ssl_mode=ssl_mode,
            ),
            analytics=AnalyticsSection(mode=analytics_mode),
            ai=AISection(
                integration=[ai_client] if ai_client else [],
                studio_backend=ai_client.value if ai_client else "off",
                intent_backend=ai_client.value if ai_client else "off",
                report_backend=ai_client.value if ai_client else "off",
                planner_backend=ai_client.value if ai_client else "off",
            ),
        )
        connector = self._connector_factory(clean_url, config)
        try:
            connection = connector.check_connection()
        finally:
            connector.dispose()

        # Do not create any files until the connection has passed validation.
        loader.create_project(config)
        store_database_url(name, clean_url)
        return config, connection

    def initialise(self, project: str, *, progress: ProgressCallback | None = None) -> SetupOutcome:
        """Scan, profile, generate, and validate a project's semantic layer."""

        notify = progress or (lambda _step, _message: None)
        config = loader.load_config(project)
        database_url = resolve_database_url(config.database, project)
        metadata = MetadataRepository(paths.metadata_path(project))
        warnings: list[str] = []
        try:
            notify("scan", "Scanning database schemas and relationships")
            started = utcnow()
            scan_connector = self._connector_factory(database_url, config)
            try:
                scan_result = SchemaScanner(scan_connector, config.database).scan()
            finally:
                scan_connector.dispose()
            summary = metadata.save_scan(scan_result, started_at=started, finished_at=utcnow())

            if config.profiling.enabled:
                notify("profile", "Profiling bounded, privacy-safe samples")
                profile_connector = self._connector_factory(database_url, config)
                try:
                    profile_result = Profiler(
                        profile_connector, metadata, config.profiling
                    ).profile()
                finally:
                    profile_connector.dispose()
                metadata.save_profiles(profile_result)

            notify("semantic", "Generating schema-grounded metrics and dimensions")
            semantic = SemanticRepository(paths.semantic_path(project))
            profiles = {p.qualified_column: p for p in metadata.list_column_profiles()}
            generated = generate_semantic(metadata.list_table_details(), profiles, semantic.load())
            semantic.save(generated.layer)

            notify("validate", "Validating the semantic layer")
            issues = validate_semantic(generated.layer, SchemaIndex.from_repository(metadata))
            warnings.extend(issue.message for issue in issues)
            notify("complete", "Setup complete")
            return SetupOutcome(
                project=project,
                tables=summary.table_count,
                columns=summary.column_count,
                metrics=len(generated.layer.metrics),
                dimensions=len(generated.layer.dimensions),
                warnings=warnings,
            )
        finally:
            metadata.dispose()
