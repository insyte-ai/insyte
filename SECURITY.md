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
- **Redacted, structured logs.** All logging passes through a redaction filter that masks
  connection URLs and sensitive fields (passwords, tokens, API keys).

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
