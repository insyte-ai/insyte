# Security Policy

Insyte is built to analyse production databases without putting them at risk. Security is a
core feature, not an afterthought.

## Design guarantees

- **Read-only by design.** Insyte is intended to be used with a dedicated read-only database
  account. Every query runs inside a `READ ONLY` transaction with
  `statement_timeout`, `lock_timeout`, and `idle_in_transaction_session_timeout` applied.
- **Credentials never leave your machine.** The database URL is resolved from the configured
  environment variable or a project-local mode-`0600` secret file only when needed. It is never
  written to `config.yaml`, logged, returned by an MCP tool, or included in an AI prompt.
- **AI clients cannot bypass the query engine.** Claude Code, Codex, and any other MCP client
  can only call validated tools. They cannot obtain the connection URL or execute raw,
  unvalidated SQL. SQL validation, permission checks, row limits, timeouts, PII masking, and
  audit logging apply to every path.
- **Semantic aliases cannot invent data.** Natural-language aliases generated from scanned
  metadata are routing hints only. They must point to existing metrics or dimensions, carry
  evidence, and pass semantic validation before use. Low-confidence or ambiguous aliases fail
  closed rather than silently choosing a target.
- **Semantic retrieval is non-authoritative.** Local FTS and deterministic catalog scoring only
  shortlist existing metrics and dimensions. Every model-selected ID is validated against the
  complete semantic layer before query generation; retrieval cannot create schema objects,
  values, joins, or SQL.
- **Model routing does not grant capability.** Intent, planner, and report tasks may use different
  local Claude/Codex clients, but every route has the same validated inputs and service boundary.
  Explicit fallback selects another local client; it never bypasses deterministic validation.
- **Agents use approved services only.** The internal planner can select only typed trend,
  comparison, segment, quality, and report operations. The analyst calls `AnalysisService`; no
  agent receives a connector, credential, arbitrary SQL method, or direct database access.
- **Redacted, structured logs.** All logging passes through a redaction filter that masks
  connection URLs and sensitive fields (passwords, tokens, API keys).

## Detailed reports (opt-in)

Insyte's default posture is that **AI models see only metric and dimension names, never your
data**. The optional **Detailed report** feature is the single, explicit exception, and it is
deliberately narrow:

- **Opt-in only.** Off by default; enabled per question via the Studio toggle, and globally
  gated by `ai.detailed_reports` in `config.yaml` (set it to `false` to disable entirely).
- **Aggregated results only.** What is sent is the already-computed, validated, PII-masked,
  row-limited *result* (e.g. a metric total, a breakdown by dimension, a monthly trend — capped
  at 200 rows) plus Insyte-computed metadata (data-quality flags, forecast bands). **Raw table
  rows, connection strings, and credentials are never sent.**
- **The AI only writes prose.** It receives numbers and returns commentary. It does not author
  SQL, choose what is queried, or produce any chart — every figure and chart in the report is
  computed deterministically by Insyte.
- **The report contract is validated.** The compact analyst prompt contains one canonical JSON
  schema, and model output is parsed into typed report models. Unsupported sections remain empty;
  malformed report output degrades without opening another query path.
- **Report figures are reviewed.** A deterministic critic checks every generated report figure
  against the supplied evidence payload. Claims containing unsupported figures are removed;
  if no grounded report content remains, the model report is blocked.
- **It leaves your machine.** The payload goes to your local `claude`/`codex` CLI, which sends
  it to that provider (Anthropic / OpenAI) under your own account. A one-time notice makes this
  explicit before the first report is generated.

## Semantic enrichment and aliases

`insyte semantic generate` uses scanned metadata and existing semantic objects to generate
suggested metrics, dimensions, entities, and aliases. It does not send data to an AI provider.
Aliases such as `order count -> sales_order_count` are accepted only when the target exists in
the semantic layer.

`insyte semantic enrich` is metadata-only: it receives table names, column names/types,
relationships, safe profiles, and existing semantic objects. Every proposal is checked against
the source metric, non-PII profile, and exact observed values, then stored with
`requires_confirmation: true`. It cannot execute until approved and cannot introduce unknown
tables, columns, filter values, joins, expressions, or SQL.

## Recommendations for operators

- Create a dedicated read-only role, e.g.:

  ```sql
  CREATE ROLE insyte_reader LOGIN PASSWORD '...';
  GRANT CONNECT ON DATABASE app_db TO insyte_reader;
  GRANT USAGE ON SCHEMA public TO insyte_reader;
  GRANT SELECT ON ALL TABLES IN SCHEMA public TO insyte_reader;
  ```

- Use `blocked_tables` / `blocked_columns` in `config.yaml` to keep sensitive data out of
  analysis entirely.
- Prefer `ssl_mode: require` (or stricter) for remote databases.

## Reporting a vulnerability

Please report suspected vulnerabilities privately via the repository's security advisory
process rather than a public issue. We aim to acknowledge reports promptly and will
coordinate a fix and disclosure timeline with you.
