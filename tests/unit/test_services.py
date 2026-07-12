"""Unit tests for the shared application services (Stage 10.1)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from insyte.config import loader
from insyte.config.models import InsyteConfig, ProjectSection
from insyte.exceptions import MetricNotFoundError, ProjectNotFoundError
from insyte.metadata.models import (
    ScannedColumn,
    ScannedForeignKey,
    ScannedTable,
    ScanResult,
    TableCategory,
    TableKind,
)
from insyte.metadata.repository import MetadataRepository
from insyte.query.models import QueryHistoryEntry
from insyte.semantic.models import Metric, MetricStatus, SemanticLayer
from insyte.semantic.repository import SemanticRepository
from insyte.services.conversation_service import ConversationService
from insyte.services.history_service import HistoryService
from insyte.services.metric_service import MetricService
from insyte.services.project_service import ProjectService, resolve_project_name
from insyte.services.schema_service import SchemaService


@pytest.fixture
def metadata(tmp_path: Path) -> MetadataRepository:
    repo = MetadataRepository(tmp_path / "metadata.sqlite")
    now = datetime.now(UTC)
    repo.save_scan(
        ScanResult(
            schemas={"public": None},
            tables=[
                ScannedTable(
                    schema="public",
                    name="orders",
                    kind=TableKind.table,
                    columns=[
                        ScannedColumn("id", 0, "integer", nullable=False, is_primary_key=True),
                        ScannedColumn("customer_id", 1, "integer", nullable=False),
                    ],
                    primary_key_columns=["id"],
                    foreign_keys=[
                        ScannedForeignKey("fk", ["customer_id"], "public", "customers", ["id"])
                    ],
                    category=TableCategory.fact,
                    category_confidence=0.8,
                )
            ],
            relationships=[],
        ),
        started_at=now,
        finished_at=now,
    )
    yield repo
    repo.dispose()


def test_schema_service_summary_and_search(metadata: MetadataRepository) -> None:
    service = SchemaService(metadata)
    summary = service.database_summary()
    assert summary.scanned is True
    assert summary.tables[0].name == "orders"

    matches = service.search("customer")
    assert matches and "customer_id" in matches[0].matched_columns
    assert service.get_table(None, "orders") is not None


def test_history_service(metadata: MetadataRepository) -> None:
    metadata.record_query(
        QueryHistoryEntry(
            source="direct",
            raw_sql="SELECT 1",
            normalized_sql=None,
            referenced_tables=[],
            status="ok",
        )
    )
    service = HistoryService(metadata)
    assert len(service.queries()) == 1


def test_metric_service_approve(tmp_path: Path) -> None:
    repo = SemanticRepository(tmp_path / "semantic.yaml")
    repo.save(
        SemanticLayer(
            metrics={
                "revenue": Metric(label="Revenue", expression="SUM(x)", source_table="public.t")
            }
        )
    )
    service = MetricService(repo)
    assert service.get("revenue").status is MetricStatus.suggested
    approved = service.approve("revenue")
    assert approved.status is MetricStatus.confirmed
    assert service.get("revenue").status is MetricStatus.confirmed  # persisted
    with pytest.raises(MetricNotFoundError):
        service.approve("ghost")


def test_conversation_service_roundtrip(metadata: MetadataRepository) -> None:
    service = ConversationService(metadata, "demo")
    conversation = service.create("Revenue analysis")
    assert conversation.id.startswith("conv_")

    service.add_message(conversation.id, "user", "Show revenue")
    service.add_message(conversation.id, "assistant", "Revenue is up", analysis_id="an_1")
    messages = service.messages(conversation.id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[1].analysis_id == "an_1"

    assert [c.id for c in service.list_all()] == [conversation.id]
    service.save_analysis("an_1", "Show revenue", "Revenue is up", '{"summary": "Revenue is up"}')
    assert '"summary"' in (service.get_analysis("an_1") or "")

    from insyte.studio.context import ChatContext

    service.save_context(conversation.id, ChatContext(active_metric="revenue"), "an_1")
    assert service.latest_context(conversation.id).active_metric == "revenue"

    assert service.delete(conversation.id) is True
    assert service.list_all() == []


def test_autotitle_from_question(metadata: MetadataRepository) -> None:
    service = ConversationService(metadata, "demo")
    conv = service.create()  # default "New analysis"
    service.autotitle_from_question(conv.id, "tell me total sales in last month")
    assert service.get(conv.id).title == "Tell me total sales in last month"
    # A second question does not rename an already-titled conversation.
    service.autotitle_from_question(conv.id, "and by city?")
    assert service.get(conv.id).title == "Tell me total sales in last month"

    # Long questions are truncated.
    conv2 = service.create()
    service.autotitle_from_question(conv2.id, "x" * 80)
    assert service.get(conv2.id).title.endswith("…")
    assert len(service.get(conv2.id).title) <= 48


def test_project_service_open(isolated_home: Path) -> None:
    loader.create_project(InsyteConfig(project=ProjectSection(name="demo")))
    services = ProjectService.open("demo")
    try:
        assert services.config.project.name == "demo"
        assert services.schema.has_metadata() is False  # not scanned yet
        assert services.conversations.list_all() == []
    finally:
        services.dispose()


def test_resolve_project_name_errors(isolated_home: Path) -> None:
    with pytest.raises(ProjectNotFoundError):
        resolve_project_name(None)  # no projects
    loader.create_project(InsyteConfig(project=ProjectSection(name="demo")))
    assert resolve_project_name("demo") == "demo"
    with pytest.raises(ProjectNotFoundError):
        resolve_project_name("ghost")
