# Insyte

**Ask your database questions in natural language — locally, and safely.**

Insyte connects to your database with **read-only** credentials and turns natural-language
questions into safe analytics. Ask *"what were total sales last month?"* or *"revenue by
city"* from a browser workspace, a terminal UI, or your own AI tool (Claude Code / Codex) —
Insyte writes the SQL, runs it read-only, and shows the answer.

Two things are always true:

1. **AI models never see your database credentials.**
2. **Nothing can bypass Insyte's SQL validation, row limits, PII masking, or audit log** — a
   dangerous query is rejected, not executed.

## What you can do

- **Ask in natural language** — "total order value last month", "orders by payment method",
  "monthly revenue trend", "what's the expected revenue this year?"
- **Three ways to use it** — a browser workspace (`insyte studio`), a terminal UI
  (`insyte chat`), or directly from **Claude Code / Codex** over MCP.
- **Trends, breakdowns, comparisons, and forecasts** over metrics Insyte generates from your
  schema — with charts, tables, and the exact SQL on demand.
- **Detailed reports (opt-in)** — flip on "Detailed report" in Studio for an in-depth analyst
  write-up: executive summary, key insights, data-quality flags, root-cause reasoning,
  best/expected/worst forecast, and prioritized recommendations, in a visual dashboard. See the
  privacy note below for what this shares.
- **Read-only and private** — everything runs on your machine against your database; the raw
  connection URL never leaves your computer.

### Detailed reports & your privacy

Everything above keeps your data on your machine. The one **opt-in** exception is the
**Detailed report**: to write analyst commentary, Insyte sends the *already-aggregated,
PII-masked result* of your query (e.g. totals by city — never raw rows, never credentials) to
your local `claude`/`codex` CLI, which forwards it to that provider. The AI only writes prose —
it never sees credentials, never authors SQL, and every chart is built by Insyte from real
numbers. It's off by default, shows a one-time notice the first time you enable it, and can be
turned off entirely with `ai.detailed_reports: false` in `config.yaml`.

## Install & set up

Easiest — **pipx** installs Insyte in its own isolated environment (no virtual-env to manage):

```bash
pipx install insyte
insyte init          # asks for your read-only DB URL and which AI tool, then sets everything up
```

<sub>No pipx yet? `brew install pipx` (macOS) or `python -m pip install --user pipx && pipx ensurepath`, then reopen your terminal.</sub>

Prefer plain pip? Install into a virtual environment:

```bash
python -m venv .venv && source .venv/bin/activate
pip install insyte
insyte init
```

`insyte init` walks you through it:

1. Enter your **read-only database URL** (stored once in a `0600` file — never in config,
   never logged, never sent to an AI).
2. Pick your **AI tool** — Claude Code, Codex, or none.
3. Insyte then **connects, scans the schema, generates metrics, and wires up your AI tool** —
   no scripts, no environment variables.

Then use it:

```bash
insyte studio        # browser workspace at http://127.0.0.1:3838
insyte chat          # terminal UI
insyte analyze total_amount --by city
```

**Requirements:** Python 3.11+, a PostgreSQL database, and — for natural-language questions —
the `claude` or `codex` CLI (Studio also answers metric questions without one).

### Recommended: a read-only database user

Insyte enforces read-only regardless, but a dedicated account is safest:

```sql
CREATE ROLE insyte_reader LOGIN PASSWORD '…';
GRANT CONNECT ON DATABASE your_db TO insyte_reader;
GRANT USAGE ON SCHEMA public TO insyte_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO insyte_reader;
```

## Use it from Claude Code / Codex

`insyte init` already installs the Insyte MCP server into your chosen tool. Restart it, then
ask questions in plain language — Claude/Codex call Insyte's safe tools; it validates, runs
read-only, masks PII, and audits every query. (Re-run any time with `insyte mcp install claude`
or `insyte mcp install codex`.)

## Handy commands

| Command | What it does |
|---|---|
| `insyte init` | Guided setup: DB URL + AI tool → connect, scan, metrics, MCP |
| `insyte studio` | Browser workspace (localhost only) |
| `insyte chat` | Terminal UI |
| `insyte analyze <metric> --by <dimension>` | A single analysis from the CLI |
| `insyte metrics` | List the metrics Insyte generated |
| `insyte status` / `insyte doctor` | Project state / health checks |

Everything lives under `~/.insyte/projects/<name>/` (config, stored URL, scanned schema,
metrics). The connection URL is read only when needed and never written to `config.yaml`.

## Feedback

Found a bug or have an idea? Please open an issue:
**https://github.com/insyte-ai/insyte/issues** — feedback is very welcome.

## Contributing

```bash
uv venv && uv pip install -e '.[dev]'
uv run ruff check src tests && uv run mypy src && uv run pytest -q
```

## Security & license

See [SECURITY.md](SECURITY.md) for the read-only posture and how to report a vulnerability.
Licensed under **Apache-2.0** — see [LICENSE](LICENSE).

