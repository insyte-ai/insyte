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
3. Insyte then **connects, scans the schema, profiles bounded safe samples, generates and
   validates metrics, proposes reviewable derived metrics, creates short schema-grounded starter
   questions, and installs the MCP server** into your chosen tool — no scripts or environment
   variables.

Metric generation also creates safe semantic aliases from scanned metadata. For example, if
your schema has `sales_orders.order_ts`, Insyte can generate `sales_order_count` and understand
"order count" as that metric. Aliases only point at existing metrics/dimensions and include
evidence; Insyte does not invent tables, columns, values, or SQL.

The scan is fingerprinted. If a later scan changes a table or column definition, profiles from
the old shape are invalidated instead of being silently reused. Schema search uses a local
SQLite full-text index; no external vector database or metadata service is required.

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
- **Investigation Mode Lite** — ask "why did total amount change?" or a date-bound question like
  "Why did order count drop from February 2026 to March 2026?" to get a deterministic timeline:
  trend, period-aware comparison, segment breakdown, data freshness, and next questions.
- **Saved investigations** — completed investigations are saved locally. Open the
  Investigations workspace from the left sidebar to review the timeline, report, linked result,
  and export Markdown/JSON.
- **Detailed reports** — turn on the "Detailed report" tool in the composer for an analyst
  report over the computed result or investigation bundle. Only aggregated, PII-masked outputs
  are sent to your local Claude/Codex CLI; credentials and raw rows are never sent.
- **Interactive charts** — hover points/bars for values, expand charts fullscreen, and inspect
  smoothed trend lines with readable date labels.

Good investigation prompts use business terms that map to generated metrics:

```text
What caused order count to change this month?
Why did total completed orders change by store name?
Why did revenue drop last month?
Why did order count drop from February 2026 to March 2026?
```

If a phrase is ambiguous or unsupported by the scanned schema, Insyte falls back or asks for a
clearer metric instead of pretending the data exists.

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
insyte semantic generate                          # refresh metrics, dimensions, and aliases
insyte semantic enrich --backend codex            # propose filtered metrics for review
insyte semantic questions --backend codex         # refresh Studio starter questions
```

Derived proposals inherit an existing metric expression and can filter only exact, observed,
non-PII values from that metric's source table. They are blocked until approved in Studio's
Metrics page or with `insyte metrics approve <name>`.

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
which insyte         # confirms which installation is being executed
insyte --version     # compare with the version you installed or built
insyte doctor        # checks Python, packages, config, and whether the DB URL is set
insyte status        # shows the active project and last scan (no DB connection)
```

When testing unreleased workspace changes, activate the repository virtual environment or invoke
its executable explicitly:

```bash
cd /path/to/insyte
.venv/bin/pip install -e .
.venv/bin/insyte --version
.venv/bin/insyte studio
```

If `which insyte` points to another Python, pipx, Conda, or system installation, that command may
serve an older bundled Studio UI and run an older initialization flow even when the repository
contains newer code.

Logs are under `~/.insyte/projects/<name>/logs/` as structured JSON, with credentials and PII
redacted.
