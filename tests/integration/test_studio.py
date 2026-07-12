"""Integration test: the Studio API against a real PostgreSQL, through the real pipeline."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from helpers import load_ecommerce_fixture

from insyte.config import loader, paths
from insyte.config.models import DatabaseSection, InsyteConfig, ProjectSection, SSLMode
from insyte.connectors.postgres import PostgresConnector
from insyte.metadata.repository import MetadataRepository, utcnow
from insyte.metadata.scanner import SchemaScanner
from insyte.services.project_service import ProjectService
from insyte.studio.app import create_studio_app

_TEST_URL = os.environ.get("INSYTE_TEST_DATABASE_URL")
_FIXTURES = Path(__file__).parent.parent / "fixtures"

pytestmark = pytest.mark.skipif(
    not _TEST_URL, reason="Set INSYTE_TEST_DATABASE_URL to run PostgreSQL integration tests."
)


@pytest.fixture
def client(isolated_home: Path, monkeypatch: pytest.MonkeyPatch):
    assert _TEST_URL is not None
    load_ecommerce_fixture(_TEST_URL, _FIXTURES / "ecommerce.sql")

    monkeypatch.setenv("INSYTE_DATABASE_URL", _TEST_URL)
    loader.create_project(
        InsyteConfig(
            project=ProjectSection(name="demo"),
            database=DatabaseSection(ssl_mode=SSLMode.prefer),
        )
    )
    connector = PostgresConnector(
        _TEST_URL,
        DatabaseSection(ssl_mode=SSLMode.prefer),
        InsyteConfig(project=ProjectSection(name="d")).query,
    )
    repo = MetadataRepository(paths.metadata_path("demo"))
    repo.save_scan(
        SchemaScanner(connector, DatabaseSection(ssl_mode=SSLMode.prefer)).scan(),
        started_at=utcnow(),
        finished_at=utcnow(),
    )
    repo.dispose()
    connector.dispose()
    import shutil

    shutil.copy(_FIXTURES / "semantic.yaml", paths.semantic_path("demo"))

    services = ProjectService.open("demo")
    with TestClient(create_studio_app(services=services)) as test_client:
        yield test_client
    services.dispose()


def _final_result(sse_text: str) -> dict:
    for block in sse_text.split("\n\n"):
        if "event: response_completed" in block:
            line = next(x for x in block.splitlines() if x.startswith("data:"))
            return json.loads(line[len("data:") :].strip())["result"]
    raise AssertionError("no response_completed")


def _ask(client: TestClient, question: str) -> dict:
    conv = client.post("/api/conversations", json={}).json()
    posted = client.post(
        f"/api/conversations/{conv['id']}/messages", json={"content": question}
    ).json()
    return _final_result(client.get(posted["stream_url"]).text)


def test_real_analysis_flow(client: TestClient) -> None:
    result = _ask(client, "completed revenue by city")
    assert result["status"] == "completed"
    assert result["contributors"]  # ranked segments from the real join
    assert result["query"]["sql"].startswith("SELECT")


def test_history_updated_by_studio(client: TestClient) -> None:
    # A Studio analysis runs through the shared executor and is audited (source "analytics").
    _ask(client, "payment failure rate")
    history = client.get("/api/history").json()
    assert any(q["status"] == "ok" for q in history["queries"])
