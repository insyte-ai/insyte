# Insyte + MCP (Claude Code / Codex)

Insyte exposes your database to AI clients over the **Model Context Protocol** — safely. The AI
gets a set of typed tools; it never receives your connection URL, and every query it runs goes
through Insyte's validation, permission checks, row limits, timeouts, and audit log.

## Quick start

```bash
insyte init                    # if you haven't already
insyte scan                    # build local schema metadata
insyte mcp install claude      # or: insyte mcp install codex
```

`install` shows the proposed entry, asks for confirmation, and merges it into the client's
config **without touching anything else**:

- **Claude Code** → `~/.claude.json` (JSON, `mcpServers.insyte`)
- **Codex** → `~/.codex/config.toml` (TOML, `[mcp_servers.insyte]`)

Remove it any time:

```bash
insyte mcp uninstall claude
```

## How the database URL is handled

The MCP server needs the database URL, but the **AI model must never see it**. By default the
installer does **not** write the URL into the client config; the server reads it from its own
environment (`INSYTE_DATABASE_URL`). If your client can't provide that environment, re-run with:

```bash
insyte mcp install claude --embed-secret
```

This stores the URL in the client's local config file (which the model never reads) — a
deliberate opt-in, off by default.

## The tools

| Tool | What it does |
|---|---|
| `insyte_get_database_summary` | schemas, tables, categories, last scan |
| `insyte_search_schema` | find tables/columns by substring |
| `insyte_describe_table` | columns, keys, relationships |
| `insyte_list_metrics` | semantic metrics & dimensions |
| `insyte_get_metric_definition` | one metric's full definition |
| `insyte_create_analysis_plan` | map a question → structured plan |
| `insyte_run_safe_sql` | **validate + run** a read-only query |
| `insyte_compare_periods` | current vs previous period for a metric |
| `insyte_segment_metric` | break a metric down by a dimension |
| `insyte_generate_chart_spec` | recommend a chart for a result shape |
| `insyte_get_query_history` | recent audited queries |

Every response is structured JSON. A dangerous query passed to `insyte_run_safe_sql` is
rejected with its violations and **never reaches the database** — identical behaviour to
`insyte query` on the CLI.

## MCP tools versus Studio agents

The Month 4 model router and internal agents power Studio intent, investigation planning, and
detailed reports. They do not add MCP permissions. MCP clients still receive only the tools
listed above, and internal agents cannot call `insyte_run_safe_sql` or access an MCP client's
credentials. Planner output is limited to approved analytical operations and all execution still
passes through `AnalysisService` and the existing query safety pipeline.

## Running the server directly

`insyte mcp start --project <name>` runs the stdio server (this is what the client launches for
you). It logs only to `~/.insyte/projects/<name>/logs/mcp.log` — never to stdout, which is the
protocol channel.
