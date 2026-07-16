# Contributing to Insyte

Thanks for your interest in improving Insyte! This project is built milestone by milestone
(see the roadmap in the [README](README.md)).

> **For the full architecture, how to add a feature, and how to write/run tests, read
> [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).** It's written to be usable by humans and AI
> coding agents alike (you can copy it to `CLAUDE.md` / `AGENTS.md` for auto-loading).


## Development setup

```bash
git clone https://github.com/insyte-ai/insyte
cd insyte
uv venv && source .venv/bin/activate
uv pip install -e '.[dev]'      # or: pip install -e '.[dev]'
uv run ruff check src tests && uv run mypy src && uv run pytest -q
```

## Standards

- **Type hints** on all public functions; code must pass `mypy src`.
- **Formatting & linting** via Ruff: `ruff check src tests` and `ruff format src tests`.
- **Tests** via Pytest: `pytest -q`. New behaviour needs tests.
- Keep modules small and focused; use custom exceptions (`insyte.exceptions`); never store
  secrets in code or config; never execute unvalidated SQL.
- Keep AI changes behind typed boundaries: route tasks through `nl/router.py`, validate planner
  output against the semantic catalog, call only approved services, and test deterministic
  fallback plus malformed model output.
- Dependencies are added in the milestone that first uses them — don't pull in the whole
  stack early.

## Before opening a pull request

```bash
ruff check src tests
ruff format --check src tests
mypy src
pytest -q
```

Please keep changes commit-sized and scoped to a single concern, and update the README when
you add or change a command.

## Security-sensitive changes

Anything touching connection handling, SQL validation, model routing, agent permissions,
report grounding, logging/redaction, or credential resolution deserves extra care and explicit
tests. See [SECURITY.md](SECURITY.md).
