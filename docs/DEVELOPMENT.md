# Insyte — Developer & Agent Guide

A single reference for working on Insyte: how it's built, how to run it, how to add a feature,
and how to write and run tests. It's written so a human **or an AI coding agent** (Claude Code,
Codex, etc.) can pick it up cold and be productive.

> **Using this with an AI tool:** point the agent at this file first, e.g.
> *"Read `docs/DEVELOPMENT.md`, then add feature X following the layering rules and the
> add-a-feature checklist, with tests, and finish with the quality gate."* You can also copy
> this to `CLAUDE.md` (Claude Code) or `AGENTS.md` (Codex) at the repo root so it's auto-loaded.

---

## Table of contents
1. [What Insyte is](#1-what-insyte-is)
2. [The non-negotiable safety model](#2-the-non-negotiable-safety-model)
3. [Architecture & the safe pipeline](#3-architecture--the-safe-pipeline)
4. [Repository layout](#4-repository-layout)
5. [Layering rules](#5-layering-rules)
6. [Dev environment setup](#6-dev-environment-setup)
7. [Running Insyte locally](#7-running-insyte-locally)
8. [The quality gate & running tests](#8-the-quality-gate--running-tests)
9. [Writing tests (patterns)](#9-writing-tests-patterns)
10. [How to add a new feature](#10-how-to-add-a-new-feature)
11. [Worked example: the Detailed Report feature](#11-worked-example-the-detailed-report-feature)
12. [Worked example: Semantic aliases without hallucination](#12-worked-example-semantic-aliases-without-hallucination)
13. [Worked example: Context, investigations, and saved workspace](#13-worked-example-context-investigations-and-saved-workspace)
14. [Schema retrieval and metric capabilities](#14-schema-retrieval-and-metric-capabilities)
15. [Conventions & gotchas](#15-conventions--gotchas)
16. [Publishing](#16-publishing)

---

## 1. What Insyte is

Insyte is a **local-first, open-source AI analytics tool for PostgreSQL**. A user connects with
**read-only** credentials and asks questions in natural language from a browser workspace
(`insyte studio`), a terminal UI (`insyte chat`), the CLI, or their own AI tool (Claude Code /
Codex) over MCP. Insyte translates the question to a validated query, runs it read-only, and
returns typed results (metrics, charts, tables).

- **Python 3.11+**, packaged with **hatchling**, tooled with **uv**.
- Console entry point: `insyte = "insyte.main:main"`.
- All per-project state lives under `~/.insyte/projects/<name>/` (override the root with
  `INSYTE_HOME`).

## 2. The non-negotiable safety model

These are invariants. Any change that could weaken them needs explicit tests and review.

1. **AI models never see database credentials.** The connection URL is resolved only when a
   query runs; it is **never** written to `config.yaml`, logged, or returned to any AI/MCP client.
   It lives in a `0600` file `~/.insyte/projects/<name>/.database_url`.
2. **Nothing bypasses the pipeline.** Every query passes SQL validation (AST), permission /
   blocked-column checks, row limits, timeouts, PII masking, and audit logging. A dangerous
   query is **rejected, not executed**.
3. **The AI translates, it does not author SQL.** The model turns a question into a small JSON
   *intent* (pick metric(s)/dimension), strictly validated against the real semantic layer.
   Insyte builds the SQL itself.
4. **Studio is localhost-only** (`127.0.0.1`) and rejects non-local Host headers.
5. **The one opt-in exception:** the *Detailed report* feature sends already-aggregated,
   PII-masked, row-limited **results** (never raw rows/credentials) to the local AI CLI for
   prose commentary. It's off by default, one-time-noticed, and kill-switched by
   `ai.detailed_reports`. See [SECURITY.md](../SECURITY.md).

## 3. Architecture & the safe pipeline

Insyte is layered. A question flows down through the domain and back up as a typed result:

```
Question (natural language)
  │
  ▼  semantic/catalog.py ─ exact/alias/token ranking; shortlist only known objects
  ▼  nl/ ─ deterministic parser first; task router + validated AI CLI fallback
Intent (JSON: metric, secondary_metric, mode, dimension, grain, period) ← validated against layer
  │
  ▼  query/generator.py ─ build SQL (join path via FK BFS)
  ▼  query/validator.py ─ SQLGlot AST validation (SELECT-only, blocked cols, etc.)
  ▼  query/cost_guard.py ─ inject row LIMIT
  ▼  query/executor.py ─ run in connectors/ read-only transaction (statement/lock timeouts)
  │
  ▼  analytics/ ─ engine aggregates; segmentation ranks; charts format (₹ Cr/L); forecast; report
Typed result (analytics/models.py → studio/schemas.py for the API)
```

Key domain packages:

| Package | Responsibility |
|---|---|
| `config/` | Pydantic config models, YAML load/save, `~/.insyte` paths, secret (DB URL) resolution — never stores the URL. |
| `connectors/` | Read-only DB connections (`postgres.py`, `duckdb.py`); `read_only_transaction()` with timeouts; `factory.py`. |
| `metadata/` | `scanner` (schema), `profiler` (sample column stats), `pii_detector`, `classifier` (fact/dim), `relationship_detector` (FKs), `repository` (SQLite metadata, fingerprints, and FTS catalog). |
| `semantic/` | Semantic-layer models (metrics / dimensions / entities / aliases), `generator`, `validator`, `catalog` (candidate retrieval and metric capabilities), `repository` (`semantic.yaml`). |
| `query/` | `generator` (SQL + join BFS), `validator` (SQLGlot), `cost_guard` (limits), `executor` (read-only run). |
| `analytics/` | `engine` (aggregate/timeseries/segment/opportunity/compare), `charts` (Indian ₹/Cr/L formatting), `forecast`, `segmentation`, `comparison`, `report` (detailed-report grounding). |
| `nl/` | `llm` (shell out to `claude`/`codex`; NL→intent and detailed-report prose), `router` (task-aware model selection), `periods`. The deterministic parser is `tui/intent.py`. |
| `agents/` | Typed internal Planner, Analyst, Quality, Report, and Critic agents. They exchange strict models and invoke only approved application services. |
| `services/` | Orchestration used by every interface: `project_service` (opens a project → a `ProjectServices` bundle), `analysis_service`, `schema_service`, `metric_service`, `conversation_service`, `history_service`, `export_service`. |

Interfaces (thin, call into `services/`):

| Package | Interface |
|---|---|
| `cli/` | Typer commands (one file per command); `app.py` registers them; `main.py` is the entry point. |
| `tui/` | Textual terminal UI; `controller.py` holds all logic (view-agnostic, unit-testable); `intent.py` is the deterministic NL parser. |
| `studio/` | FastAPI app (`app.py`), `routes/`, `events.py` (SSE `stream_analysis`), `schemas.py` (typed API models), `context.py` (compact chat context), `investigation.py` (deterministic Investigation Mode Lite), `static.py`; the bundled SPA lives in `studio_dist/assets/` (`app.js`, `app.css`, logos) — no build step. |
| `mcp/` | MCP server (`server.py` exposes safe tools), `tools.py` (implementations), `installer.py` (wire into Claude/Codex). |

Cross-cutting: `logging_config.py` (JSON logs + credential/PII redaction), `exceptions.py`
(`InsyteError` hierarchy).

## 4. Repository layout

```
insyte/
├── pyproject.toml          # hatchling build, deps, entry point, ruff/mypy/pytest config
├── README.md  SECURITY.md  CONTRIBUTING.md  LICENSE
├── docs/                   # QUICKSTART, PUBLISHING, mcp, this file
├── examples/               # annotated example config
├── frontend/               # OPTIONAL Vite/React scaffold (not shipped; SPA is studio_dist/)
├── src/insyte/
│   ├── main.py  __init__.py (__version__)  exceptions.py  logging_config.py
│   ├── cli/  config/  connectors/  metadata/  semantic/  query/
│   ├── analytics/  nl/  services/  tui/  mcp/
│   └── studio/            # + studio_dist/assets/ (bundled SPA, ships in the wheel)
└── tests/
    ├── conftest.py         # isolated_home autouse fixture (INSYTE_HOME → tmp)
    ├── fixtures/           # ecommerce.sql, semantic.yaml
    ├── unit/               # fast, no DB, hermetic
    ├── integration/        # need a real Postgres (INSYTE_TEST_DATABASE_URL); skipped otherwise
    └── security/           # SQL-injection / bypass attempts
```

## 5. Layering rules

- **Interfaces → services → domain.** `cli/`, `tui/`, `studio/`, `mcp/` call `services/`;
  domain packages (`analytics`, `query`, `semantic`, `metadata`, `connectors`, `config`) never
  import an interface.
- **Keep domain modules pure and DB-free where possible** so they're unit-testable without a
  database. Example: `analytics/report.py` builds the AI payload with no `studio` import and no
  network/DB access.
- **Break import cycles with lazy imports** inside functions (e.g. `nl/llm.py` imports
  `studio.schemas` lazily; `tui/controller.py` imports `nl.llm` lazily).
- **Dependencies are added in the milestone that first needs them** — don't pull the whole
  stack early. All runtime deps live in `[project.dependencies]`.

## 6. Dev environment setup

Requires **Python 3.11+** and **uv**.

```bash
uv venv                         # create ./.venv (regenerable; never commit or copy it)
uv pip install -e '.[dev]'      # editable install + dev tools (pytest, ruff, mypy, httpx)
# optional: activate so you can drop the `uv run` prefix
source .venv/bin/activate
```

Everything below can be run either as `uv run <cmd>` or, with the venv activated, `<cmd>`.

## 7. Running Insyte locally

```bash
insyte --version                # verify the active executable/package version
insyte --help                   # every command
insyte init                     # connect → scan → profile → generate → validate → enrich → questions → MCP
insyte status                   # active project summary
insyte doctor                   # environment + config health checks
insyte studio                   # browser workspace at http://127.0.0.1:3838 (localhost only)
insyte chat                     # terminal UI
insyte analyze total_amount --by city
```

**Useful environment variables:**

| Variable | Purpose |
|---|---|
| `INSYTE_HOME` | Override `~/.insyte` (tests set this to a tmp dir). |
| `INSYTE_DATABASE_URL` | Default env var name a project reads the read-only URL from. |
| `INSYTE_STUDIO_LLM` | `auto` \| `claude` \| `codex` \| `off` — which local AI CLI powers NL. `off` = deterministic parser only. Use `codex` if Claude is org-disabled. |
| `INSYTE_STUDIO_LLM_TIMEOUT` / `INSYTE_STUDIO_REPORT_TIMEOUT` | Seconds for NL / detailed-report CLI calls. Detailed investigation reports use the same report timeout. |
| `INSYTE_TEST_DATABASE_URL` | Enables the integration test suite against a real Postgres. |

Example: `INSYTE_STUDIO_LLM=codex insyte studio`.

## 8. The quality gate & running tests

**The gate every change must pass:**

```bash
uv run ruff check src tests        # lint
uv run ruff format src tests       # (or --check in CI)
uv run mypy src                    # types (must be clean)
uv run pytest -q                   # tests
```

Tooling config lives in `pyproject.toml`: ruff `line-length = 100`, rules
`E,F,I,W,UP,B,C4,SIM`; mypy on `src`; pytest `testpaths = ["tests"]`, `asyncio_mode = "auto"`.

**Test tiers:**

```bash
uv run pytest -q                              # unit + security (integration auto-skips)
uv run pytest tests/unit -q                   # just unit
uv run pytest tests/unit/test_report_llm.py -q         # one file
uv run pytest tests/unit/test_report_llm.py -q -k report   # one test/pattern
INSYTE_TEST_DATABASE_URL="postgresql://reader:pw@localhost:5432/app" uv run pytest tests/integration -q
```

- **`tests/unit/`** — fast, no network, no real DB. The autouse `isolated_home` fixture points
  `INSYTE_HOME` at a tmp dir, so nothing touches your real `~/.insyte`.
- **`tests/integration/`** — require a real Postgres via `INSYTE_TEST_DATABASE_URL`; they
  `skipif` when it's unset (you'll see them as skipped — that's expected).
- **`tests/security/`** — injection / bypass attempts against the validator and pipeline.

## 9. Writing tests (patterns)

Put the test next to its tier: unit in `tests/unit/test_<module>.py`, integration in
`tests/integration/`, bypass attempts in `tests/security/`. **New behaviour needs a test.**

Common patterns used in this codebase:

- **Isolated home (automatic).** `conftest.py` provides an autouse `isolated_home` fixture; you
  never write to the real `~/.insyte`.
- **CLI tests** use Typer's `CliRunner` and flag-driven, non-interactive commands (see
  `tests/unit/test_cli.py`).
- **Fake the DB/engine** for unit tests — inject a fake analysis engine / connector rather than
  hitting Postgres (see `tests/unit/test_studio_api.py`'s `FakeAnalysis` / `FakeConnector`, and
  `test_chat_controller.py`).
- **Never spawn a real AI CLI in tests.** Set `INSYTE_STUDIO_LLM=off` (an autouse fixture does
  this in `test_studio_api.py` / `test_chat_controller.py`), or `monkeypatch` `nl.llm._run` /
  `resolve_report` to return canned output (see `test_report_llm.py`).
- **Keep pure domain logic testable without a DB** — e.g. `test_report_context.py` builds real
  domain objects and asserts on the payload, no database.
- **Assert on structure, not prose** — check statuses, counts, chart types, formatted values
  (₹/Cr/L), not exact sentences.

Minimal unit test shape:

```python
def test_quality_flags_severity_and_table_filter() -> None:
    profiles = [_profile("orders", "discount", null_fraction=0.6)]  # critical
    flags = data_quality_flags(profiles, {"public.orders"})
    assert flags[0]["severity"] == "critical"
```

## 10. How to add a new feature

Work **domain-up**, one self-contained step at a time, keeping the gate green after each:

1. **Domain first.** Add pure logic + typed models in the right domain package
   (`analytics/`, `query/`, `semantic/`, …). No interface imports. Add unit tests immediately.
2. **Schemas / models.** If it crosses the API, add typed models in `studio/schemas.py`
   (all-optional fields degrade gracefully) and/or `analytics/models.py`.
3. **Wire into a service** (`services/`) if interfaces need to share it.
4. **Expose on the interface(s):** a `cli/` command, a `studio/` route + `events.py` SSE, a
   `tui/controller.py` branch, and/or an `mcp/` tool. Keep interfaces thin.
5. **Frontend** (Studio only): edit `studio_dist/assets/app.js` / `app.css`. It's a
   dependency-free SPA (no build). Verify JS syntax with `node --check`, keep chart controls
   keyboard-accessible, and do a real browser visual check.
6. **Docs & safety:** update `README.md` / `SECURITY.md` if behaviour or the data-boundary
   changes.
7. **Gate + packaging:** `ruff` + `mypy` + `pytest` clean; if you added a bundled asset, add a
   wheel-contents check in `.github/workflows/release.yml` and confirm `uv build` ships it.

**Checklist before you call it done:**
- [ ] New behaviour has tests (unit; integration/security if relevant).
- [ ] Type hints everywhere; `mypy src` clean.
- [ ] `ruff check` + `ruff format` clean.
- [ ] Safety invariants (§2) preserved; credentials/PII never logged or sent.
- [ ] Graceful degradation on failure (feature off ⇒ base path unaffected).
- [ ] Docs updated; `uv build` still bundles any new assets.

## 11. Worked example: the Detailed Report feature

A concrete trace of the process above (opt-in AI analyst report over a result):

1. **Persona + schemas** — `nl/report_skill.md` (the analyst prompt/contract) and
   `DetailedReport` models in `studio/schemas.py`; opt-in `MessageRequest.detailed` flag. The
   prompt is precedence-driven and compact, with one canonical JSON schema instead of repeated
   field instructions. `test_report_llm.py` parses that embedded schema and verifies every
   top-level and nested field against the Studio response contract.
2. **Pure grounding** — `analytics/report.py`: `build_report_context()` assembles the
   masked, ≤200-row payload; `data_quality_flags()` from the profiler/PII detector;
   `forecast_bands()` best/expected/worst from real monthly actuals. **No DB, no `studio`
   import** → fully unit-tested (`test_report_context.py`).
3. **AI call** — `nl/llm.py`: `build_report_prompt()`, `resolve_report()` (+ robust JSON
   extraction/validation), sharing `_run()` with the intent path. Returns `None` on any failure
   (`test_report_llm.py`, all with fake backends).
4. **Wire-up** — `studio/events.py` threads the flag through `stream_analysis`, runs the report
   after the result, emits `report_generating`/`report_ready` SSE, attaches `result.report`,
   and degrades softly. Routes carry `detailed` (`test_studio_api.py`).
5. **Frontend** — `app.js`/`app.css`: the "+" menu, the removable chip, the ↑/■ send-stop
   button, and the report dashboard (charts derived only from the real result).
6. **Docs + release** — README/SECURITY document the data boundary; `release.yml` verifies the
   wheel bundles `report_skill.md`.

Optimising the persona must not change the response shape. Keep grounding, analytical-frame,
quality, materiality, causal, forecast, recommendation, ROI, confidence, severity, item-limit,
and empty-field rules represented once; update the Pydantic models and schema-contract tests
together for any intentional response change.

The invariant that keeps an ambitious feature honest: **data & charts are deterministic
(Insyte), narrative is the AI's** — the model never authors SQL or invents a number.

## 12. Worked example: Semantic aliases without hallucination

The semantic alias layer makes Studio smarter about obvious business wording while preserving
the same no-hallucination boundary as the rest of the system.

### Data model

`semantic/models.py` defines `SemanticAlias`:

```python
class SemanticAlias(BaseModel):
    target: str
    target_type: str = "metric"  # metric | dimension
    confidence: float = 0.5
    evidence: list[str] = Field(default_factory=list)
    status: MetricStatus = MetricStatus.suggested
```

`SemanticLayer.aliases` is a dictionary keyed by normalized human phrase:

```yaml
aliases:
  order count:
    target: sales_order_count
    target_type: metric
    confidence: 0.93
    evidence:
      - metric:sales_order_count
      - expression:COUNT(*)
    status: suggested
```

Aliases are routing hints only. They never define a new table, column, value, metric
expression, or SQL query.

### Generation

`semantic/generator.py` creates aliases after it has generated or merged entities, metrics, and
dimensions:

1. It adds aliases for metric names and labels (`sales_order_count`, `Sales order count`).
2. It strips aggregate prefixes for natural phrasing (`total_completed_orders` →
   `completed orders`).
3. It adds obvious count aliases from countable entity tables (`sales_orders` →
   `sales_order_count`, `order count`).
4. It adds dimension aliases from dimension names, labels, and source columns.

Count metrics are generated not only for fact/event tables, but also for timestamped business
entity tables with a primary key. This is why a `sales_orders` table with `order_ts` can produce
a time-aware `sales_order_count` metric, which is better for investigations than an aggregate
field with no time column.

### Validation

`semantic/validator.py` validates aliases against the current semantic layer:

- `target_type: metric` must point at an existing metric.
- `target_type: dimension` must point at an existing dimension.
- unknown target types are errors.

This is the main anti-hallucination guard: an alias cannot point at something that does not
already exist in `semantic.yaml`.

### Parsing

`tui/intent.py` uses aliases only after exact metric matching fails:

1. Exact metric name/label wins.
2. High-confidence alias matches are considered next.
3. Aliases below `_AUTO_ALIAS_CONFIDENCE` are ignored.
4. Multiple close-confidence aliases with different targets are treated as ambiguous and return
   unknown instead of silently choosing.

The same alias-aware parser is used by Studio, TUI, and MCP-facing analysis paths.

### Safety rules for AI enrichment

`semantic enrich --backend codex|claude` obeys these rules:

- send metadata only: table names, column names/types, relationships, safe profiles, existing
  metrics/dimensions/aliases;
- never send credentials or raw rows;
- require structured JSON/YAML output;
- validate every suggested target against scanned metadata and `semantic.yaml`;
- reject filters unless the value came from safe low-cardinality profiles;
- keep suggestions as `suggested`, not `confirmed`;
- preserve evidence for every accepted alias.

AI may label and connect existing facts. Deterministic code must verify every reference before
the alias is usable.

### Tests

Relevant tests:

- `tests/unit/test_semantic.py` — YAML load/save round-trip for aliases.
- `tests/unit/test_semantic_generator.py` — alias generation, `order count` mapping, validation
  errors for bad targets.
- `tests/unit/test_intent.py` — alias resolution, low-confidence rejection, ambiguity guard.

## 13. Worked example: Context, investigations, and saved workspace

Studio keeps conversations useful without widening the safety boundary:

1. **Context snapshots** — `studio/context.py` records compact active metric, dimension,
   period, report mode, recent turns, and analysis summaries. `conversation_service.py` persists
   snapshots so follow-ups resolve after page reloads.
2. **Deterministic planner** — `studio/investigation.py` detects why/how/change questions and
   builds a fixed plan: monthly trend, current-vs-previous comparison, segment breakdown,
   freshness/data-quality review, and final summary. When a user names explicit months, for
   example "from February 2026 to March 2026", the plan keeps those periods and does not fall
   back to the current calendar month.
3. **Safe execution** — every step calls `AnalysisService`; no investigation code writes SQL or
   touches credentials directly. Missing time columns or dimensions become skipped timeline
   steps with readable limitations.
4. **Detailed investigation reports** — when the Studio toggle is on, the completed
   investigation bundle is passed to the existing `nl.llm.resolve_report()` analyst skill. The
   model receives only grounded aggregate outputs and returns the same `DetailedReport` schema.
5. **Frontend timeline and charts** — `app.js` renders investigation timelines and interactive
   charts with fullscreen expansion, hover tooltips, readable date labels, and smooth trend
   lines.
6. **Saved investigations** — completed investigation results are saved automatically in the
   project metadata database. The persistence model is:
   `SavedInvestigationRecord(id, project, analysis_id, conversation_id, title, summary,
   question, result_json, created_at, updated_at)`.
7. **Routes** — `studio/routes/investigations.py` exposes:
   - `GET /api/investigations`
   - `GET /api/investigations/{id}`
   - `POST /api/investigations/{id}/rename`
   - `DELETE /api/investigations/{id}`
8. **Workspace UI** — `studio_dist/assets/app.js` has route-aware rendering for
   `#/investigations` and `#/investigations/<id>`, a left saved-investigation list, center
   result/report view, right context panel, and client-side Markdown/JSON exports.
9. **Report reading modes** — detailed reports are grouped into Executive, Analyst,
   Data Quality, and Actions panes. This is frontend grouping over the same `DetailedReport`
   schema, not a new AI output type.

Saved investigations reuse the already persisted structured `AnalysisResult` JSON. They do not
create a second query path and do not store credentials.

## 14. Schema retrieval and metric capabilities

Guided initialization runs the metadata stages in dependency order:

```text
connect -> scan -> profile -> semantic generate -> semantic validate -> enrich -> questions -> MCP install
```

`metadata/repository.py` hashes table and column shape into `schema_fingerprint`. A successful
profile stores the fingerprint it was built from. When a later scan changes the shape, table,
column, and PII profiles are deleted atomically so semantic generation and investigations cannot
consume stale statistics. Existing project databases are adopted on first open, and new tables
are created through SQLAlchemy's additive `create_all()` path.

The repository also builds `search_documents` plus an optional SQLite FTS5 index from scanned
table names, column names, data types, comments, categories, and relationship neighbors.
`SchemaService.search()` uses BM25-ranked FTS results and falls back to portable substring search
when the local SQLite build has no FTS5 support or an old project has not been rescanned.

`semantic/catalog.py` performs deterministic hybrid retrieval over metric/dimension names,
labels, expressions, source fields, aliases, and non-PII profile values. `nl/llm.resolve()` sends
only the top candidates to Claude/Codex, but validates returned IDs against the **complete**
`SemanticLayer`. Retrieval grants no query capability and cannot create a metric, dimension,
table, column, join, value, or SQL expression.

After semantic validation, guided init asks the selected local Claude/Codex CLI for up to four
Studio starter questions. The model receives metric and dimension IDs, labels, and whether each
metric supports time analysis; it receives no rows or credentials. Each response is limited to
10 words and is persisted in `semantic.yaml` only after exact metric/dimension IDs, mode
capabilities, wording vocabulary, and duplicate checks pass. Invalid output is discarded and
existing questions are preserved. `insyte semantic questions --backend claude|codex` regenerates
them explicitly.

Before starter-question generation, `semantic enrich` may propose derived metrics from existing
base metrics and low-cardinality profile values. The model returns IDs and values only; it cannot
provide SQL, expressions, joins, tables, or unobserved filter values. `semantic/proposals.py`
checks the source-table boundary, PII status, observed values, aliases, and identifiers, then
persists accepted proposals with `requires_confirmation: true`. These metrics are excluded from
starter questions and execution until approved.

At runtime, `semantic/qualifiers.py` compares material question terms with the selected metric,
filters, aliases, and dimension. A plan with unresolved terms is routed through the grounded AI
resolver instead of executing the base metric. The resolver can return a clarification and the
same constrained proposal type. Studio persists the proposal and exposes approval on the Metrics
page; approval confirms both the metric and its aliases. SQL remains generated by SQLGlot from the
validated semantic model.

`semantic/repository.py` caches the parsed YAML by modification time and size. Every load returns
a deep copy, and external file edits invalidate the cache, so the optimization cannot turn an
unsaved mutation or stale file into shared process state.

The same catalog builds a metric capability record:

```text
metric -> source table -> time column/profiled range
       -> directly or FK-neighbor dimensions -> cardinality/PII filtering
```

Investigation planning prefers reachable low/medium-cardinality dimensions, excludes profiled
PII and unique/high-cardinality fields, and flags explicit periods outside sampled time coverage.
Profile bounds are evidence, not an authoritative row-count claim, so they produce a limitation
rather than silently skipping or changing a requested comparison.

A separate vector database is intentionally not part of this path. For a local catalog of tens
to thousands of objects, exact matching plus FTS is faster to operate and easier to audit.
Embeddings may later be added as an optional candidate reranker, but their output must pass the
same exact-ID validation and ambiguity checks.

Relevant tests:

- `tests/unit/test_metadata_repository.py` - fingerprints, profile invalidation, FTS documents.
- `tests/unit/test_semantic_catalog.py` - alias retrieval, narrowing, joins, cardinality, coverage.
- `tests/unit/test_nl_llm.py` - final fail-closed intent validation.

## 15. Model router and internal agents

`nl/router.py` turns AI selection into an explicit task-level decision. `AISection` supports
`intent_backend`, `planner_backend`, `report_backend`, and `fallback_backend`; accepted values are
`auto`, `claude`, `codex`, and `off`. `studio_backend` remains as a compatibility default. Each
route logs the task, requested backend, installed candidates, fallback, and whether execution is
deterministic. Prompts, result rows, credentials, and chain-of-thought are not logged.

The internal workflow has five narrow roles:

1. `PlannerAgent` may select only `trend`, `comparison`, `segment`, `quality`, and `report`. Its
   metric and dimension must exist in `SemanticCatalog`; time operations require a time column,
   segment requires a reachable dimension, and unknown fields or extra JSON keys are rejected.
2. `AnalystAgent` is a facade over `AnalysisService`. It exposes trend, comparison, and segment
   calls and has no connector, credential, raw SQL, or general query method.
3. `QualityAgent` reads persisted `ColumnProfile` and `DataFreshness` facts. It reports only
   confirmed null/freshness issues relevant to the selected metric.
4. `ReportAgent` invokes the configured report route with the existing bounded aggregate payload
   and validates the response as `DetailedReport`.
5. `CriticAgent` compares every report figure with the evidence payload. Unsupported figures block
   the model report; investigations fall back to the deterministic structured summary and normal
   reports surface a grounding warning.

Studio emits `model_routed` and `report_critic_completed` SSE events for observable routing and
review outcomes. If planner output is absent or invalid, `InvestigationService.plan()` retains its
deterministic plan. If no intent backend exists, Studio returns its existing clarification path.
No agent can widen query permissions or bypass SQLGlot validation, execution limits, PII masking,
or audit logging.

Relevant tests:

- `tests/unit/test_model_router.py` - task routes, legacy compatibility, explicit fallback.
- `tests/unit/test_agents.py` - invalid plans, critic blocking, deterministic quality evidence.
- `tests/unit/test_investigation.py` - approved-service execution and deterministic plans.
- `tests/unit/test_studio_api.py` - routed intent/report behavior and SSE integration.

## 16. Conventions & gotchas

- **Type hints** on all public functions; code must pass `mypy src`.
- **Custom exceptions** from `insyte.exceptions` (`InsyteError` subclasses) — don't raise bare
  `Exception`.
- **Never** log or persist credentials, full URLs, or PII — the logging redaction filter is a
  backstop, not a license.
- **Currency is Indian**: `analytics/charts.py` formats ₹ with crore/lakh (`Cr`/`L`), and the
  SPA mirrors it in `compact()`. Don't reintroduce `M`/`B`.
- **Semantic-layer dimensions must be FK-joinable.** The query generator finds a join path by
  BFS over scanned foreign keys; a dimension pointing at a **view** (no FKs) can't be joined and
  will raise `JoinPathError`. Point dimensions at real, FK-connected tables.
- **Semantic aliases must never invent objects.** An alias is valid only if it points to an
  existing metric or dimension. Keep confidence thresholds conservative; ambiguous aliases
  should fail closed and let the AI fallback or user clarify.
- **Count metrics need useful time columns for investigations.** A count over a timestamped
  business entity table is often preferable to a pre-aggregated measure with no `time_column`
  because Investigation Mode can run trend and current-vs-previous steps.
- **Explicit month comparisons are period-aware, not inferred.** Investigation Mode only treats
  named month/year pairs as historical comparisons when they are present in the question; it does
  not invent date ranges or assume a hidden business calendar.
- **Saved investigations live in metadata SQLite.** Adding/changing these tables means old
  projects may need `Base.metadata.create_all()` to add new tables on Studio startup. Do not
  make this path require a live warehouse or AI backend.
- **The SPA has no build step** — edit `studio_dist/assets/*` directly; it ships in the wheel
  (`[tool.hatch.build.targets.wheel]`). Static assets are served with ETag revalidation; a
  browser may need a hard refresh after edits. Run `node --check
  src/insyte/studio_dist/assets/app.js` after non-trivial JS edits.
- **Verify the executable before UI testing.** `which insyte` may resolve to a global, Conda, or
  pipx package instead of the editable workspace. Use `.venv/bin/insyte` when validating local
  init, report-prompt, or bundled Studio changes.
- **macOS filesystem is case-insensitive** — project name/dir matching is case-insensitive on
  purpose; keep it that way.

## 17. Publishing

Full checklist in [PUBLISHING.md](PUBLISHING.md). Short version:

```bash
# bump version in pyproject.toml + src/insyte/__init__.py first
rm -rf dist build && uv build
python -m zipfile -l dist/*.whl | grep -E "report_skill.md|studio_dist/assets/app.js"  # sanity
uv publish --token "pypi-…"        # or Trusted Publishing via a v* tag + release.yml
git tag vX.Y.Z && git push origin vX.Y.Z
```
