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
- **Trends, breakdowns, opportunity segments, comparisons, and forecasts** over metrics Insyte
  generates from your schema — with charts, tables, and the exact SQL on demand.
- **Smart semantic aliases** — during semantic generation, Insyte creates safe natural-language
  aliases from scanned tables, columns, metrics, and dimensions. "order count" can resolve to a
  real `sales_order_count` metric when the `sales_orders` table exists, but aliases only point
  at existing semantic objects and carry evidence.
- **Profile-aware schema retrieval** — guided setup safely profiles bounded samples and
  fingerprints the scanned schema. SQLite full-text search ranks structural metadata, while a
  deterministic semantic catalog ranks metrics, dimensions, aliases, and safe profile evidence.
  Only relevant known objects are offered to the AI resolver; its answer is still validated
  against the complete semantic layer.
- **Investigation Mode Lite** — ask broader questions like "why did total amount change?" or
  explicit comparisons like "Why did order count drop from February 2026 to March 2026?" and
  Studio runs a safe, multi-step investigation: trend, period-aware comparison, segment
  breakdown, freshness checks, and next questions.
- **Saved investigations** — completed Studio investigations are saved locally with their
  timeline, report, original question, and linked analysis result. Reopen them from the
  Investigations workspace and export Markdown or JSON.
- **Detailed reports (opt-in)** — flip on "Detailed report" in Studio for an in-depth analyst
  write-up: executive summary, key insights, data-quality flags, root-cause reasoning,
  evidence/counter-evidence, best/expected/worst forecast, and prioritized recommendations, in a
  visual dashboard. Investigation questions can use the same analyst report skill over the
  grounded investigation bundle. See the privacy note below for what this shares.
- **Interactive charts** — charts include hover tooltips, readable date labels, expandable
  fullscreen views, and smooth trend lines for faster inspection.
- **Conversation context** — Studio remembers compact metric, dimension, period, and result
  context so follow-up questions like "same metric last month" resolve more reliably.
- **Read-only and private** — everything runs on your machine against your database; the raw
  connection URL never leaves your computer.

### Detailed reports & your privacy

Everything above keeps your data on your machine. The one **opt-in** exception is the
**Detailed report**: to write analyst commentary, Insyte sends the *already-aggregated,
PII-masked result* of your query, or a grounded investigation bundle built from those aggregate
results, to your local `claude`/`codex` CLI, which forwards it to that provider. The AI only
writes prose — it never sees credentials, never authors SQL, and every chart is built by Insyte
from real numbers. It's off by default, shows a one-time notice the first time you enable it,
and can be turned off entirely with `ai.detailed_reports: false` in `config.yaml`.

### Smart aliases without hallucination

Insyte's semantic layer can understand obvious business phrasing without making up data. The
semantic generator creates aliases such as `order count -> sales_order_count` only when the
target metric already exists, and every alias stores evidence such as the metric name and
expression. The parser uses high-confidence aliases after exact metric matching fails; low
confidence or ambiguous aliases do not run silently.

AI-assisted enrichment can be added later as a bounded review step, but the same rule applies:
AI may suggest labels and aliases over scanned metadata, never invent tables, columns, values,
or SQL.

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
3. Insyte then **connects, scans and profiles the schema, generates and validates metrics,
   proposes grounded derived metrics for review, creates concise schema-grounded starter
   questions, and wires up your AI tool** — no scripts, no environment variables.

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
| `insyte init` | Guided setup: DB URL + AI tool → connect, scan, profile, generate, validate, questions, MCP |
| `insyte scan` / `insyte profile` | Refresh structural metadata / bounded safe column profiles |
| `insyte studio` | Browser workspace (localhost only) |
| `insyte chat` | Terminal UI |
| `insyte analyze <metric> --by <dimension>` | A single analysis from the CLI |
| `insyte metrics` | List the metrics Insyte generated |
| `insyte semantic generate` | Regenerate suggested metrics, dimensions, entities, and safe aliases from scanned metadata |
| `insyte semantic enrich` | Ask the local AI CLI for profiled-value-derived metric proposals; proposals remain blocked until approved |
| `insyte semantic questions` | Regenerate short Studio starter questions with the selected local AI CLI |
| `insyte semantic validate` | Verify every semantic object against the latest scanned schema |
| `insyte status` / `insyte doctor` | Project state / health checks |

Everything lives under `~/.insyte/projects/<name>/` (config, stored URL, scanned schema,
metrics, aliases, conversations, saved investigations). The connection URL is read only when
needed and never written to `config.yaml`.

When a question contains an undefined qualifier such as "positive", "failed", or "active",
Insyte does not drop the qualifier and run the base metric. It may propose a derived metric using
an exact non-PII profiled field and observed values, but that metric remains non-executable until
you approve it from Studio's Metrics page or with `insyte metrics approve <name>`.

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
