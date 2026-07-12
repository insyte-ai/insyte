# Security Policy

Insyte is built to analyse production databases without putting them at risk. Security is a
core feature, not an afterthought.

## Design guarantees

- **Read-only by design.** Insyte is intended to be used with a dedicated read-only database
  account. From Milestone 2 onward every query runs inside a `READ ONLY` transaction with
  `statement_timeout`, `lock_timeout`, and `idle_in_transaction_session_timeout` applied.
- **Credentials never leave your machine.** The database URL is read from an environment
  variable (or, later, your OS keychain) only when a connection is actually needed. It is
  **never** written to `config.yaml`, **never** logged, and **never** returned to an AI
  client or MCP tool.
- **AI clients cannot bypass the query engine.** Claude Code, Codex, and any other MCP client
  can only call validated tools. They cannot obtain the connection URL or execute raw,
  unvalidated SQL. SQL validation, permission checks, row limits, timeouts, PII masking, and
  audit logging apply to every path (from Milestone 4 onward).
- **Semantic aliases cannot invent data.** Natural-language aliases generated from scanned
  metadata are routing hints only. They must point to existing metrics or dimensions, carry
  evidence, and pass semantic validation before use. Low-confidence or ambiguous aliases fail
  closed rather than silently choosing a target.
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
- **It leaves your machine.** The payload goes to your local `claude`/`codex` CLI, which sends
  it to that provider (Anthropic / OpenAI) under your own account. A one-time notice makes this
  explicit before the first report is generated.

## Semantic enrichment and aliases

`insyte semantic generate` uses scanned metadata and existing semantic objects to generate
suggested metrics, dimensions, entities, and aliases. It does not send data to an AI provider.
Aliases such as `order count -> sales_order_count` are accepted only when the target exists in
the semantic layer.

If AI-assisted semantic enrichment is added later, it must remain metadata-only: table names,
column names/types, relationships, safe profiles, and existing semantic objects. AI suggestions
must be validated before use and must never introduce unknown tables, columns, filter values, or
SQL.

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
