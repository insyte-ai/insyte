# Insyte — Quickstart

Insyte lets you ask your database questions in plain English — through a browser workspace, a
terminal UI, or your own AI tool (Claude Code / Codex) — with **read-only**, safe queries.

Two things are always true: **AI models never see your database credentials**, and **nothing
can bypass Insyte's SQL validation, row limits, PII masking, or audit log**.

Requires **Python 3.11+** and a **PostgreSQL** database.

---

## 1. Install

Easiest — **pipx** (isolated, no virtual-env to manage):

```bash
pipx install insyte
```

<sub>No pipx? `brew install pipx` (macOS) or `python -m pip install --user pipx && pipx ensurepath`, then reopen your terminal.</sub>

Or with plain pip in a virtual environment:

```bash
python -m venv .venv && source .venv/bin/activate
pip install insyte
```

## 2. Create a read-only database user (recommended)

Insyte enforces read-only regardless, but a dedicated account is safest:

```sql
CREATE ROLE insyte_reader LOGIN PASSWORD '…';
GRANT CONNECT ON DATABASE your_db TO insyte_reader;
GRANT USAGE ON SCHEMA public TO insyte_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO insyte_reader;
```

## 3. Set up — one command

```bash
insyte init
```

`insyte init` walks you through everything:

1. **Database URL** — paste your read-only URL. It's stored once in a `0600` file
   (`~/.insyte/projects/<name>/.database_url`) — never in `config.yaml`, never logged, never
   sent to an AI. (Advanced: choose "environment variable" instead, or pass `--db-url`.)
2. **AI tool** — Claude Code, Codex, both, or none.
3. Insyte then **connects, scans the schema, generates metrics, and installs the MCP server**
   into your chosen tool — no scripts, no environment variables.

## 4. Ask questions

### Browser workspace
```bash
insyte studio        # http://127.0.0.1:3838 (localhost only)
```
Type questions like *"total order value last month"*, *"orders by payment method"*,
*"what's the expected revenue this year?"*. Free-form questions use your local Claude/Codex to
interpret them; Insyte runs the SQL safely and shows the answer (with a chart when useful).

Studio also supports:

- **Follow-up context** — ask a metric question, then follow with "same metric last month" or
  "now by payment status".
- **Investigation Mode Lite** — ask "why did total amount change?" to get a deterministic
  timeline: trend, current-vs-previous comparison, segment breakdown, data freshness, and next
  questions.
- **Detailed reports** — turn on the "Detailed report" tool in the composer for an analyst
  report over the computed result or investigation bundle. Only aggregated, PII-masked outputs
  are sent to your local Claude/Codex CLI; credentials and raw rows are never sent.
- **Interactive charts** — hover points/bars for values, expand charts fullscreen, and inspect
  smoothed trend lines with readable date labels.

### Terminal UI
```bash
insyte chat
```

### From Claude Code / Codex (MCP)
`insyte init` already installed the MCP server. Restart your AI tool and ask in plain language —
it calls Insyte's safe tools; Insyte validates, runs read-only, masks PII, and audits every
query. Re-install any time with `insyte mcp install claude` (or `codex`).

### Direct CLI
```bash
insyte analyze total_amount --grain month        # time series + chart
insyte analyze total_amount --by city            # segment + chart
insyte metrics                                    # list the generated metrics
```

## Safety — try it

Every one of these is **blocked** before any query reaches the database:

```bash
insyte query "DELETE FROM orders"                      # not a SELECT
insyte query "SELECT pg_sleep(120)"                    # unsafe function
insyte query "SELECT 1; DROP TABLE orders"             # multiple statements
```

Configure blocked tables/columns in `config.yaml` (e.g. `users.password_hash`) and Insyte
refuses to read them — from the CLI, the TUI, and AI clients alike.

## Troubleshooting

```bash
insyte doctor        # checks Python, packages, config, and whether the DB URL is set
insyte status        # shows the active project and last scan (no DB connection)
```

Logs are under `~/.insyte/projects/<name>/logs/` as structured JSON, with credentials and PII
redacted.
