# Publishing Insyte to PyPI — migration & release plan

This is the checklist to turn Insyte into a real, installable package on PyPI so anyone can:

```bash
pip install insyte
insyte init         # guided: asks for read-only DB URL + which AI tool, does everything
insyte studio       # (or `insyte chat`) — ready to use
```

> **Onboarding is `insyte init`** (implemented). It creates the project, stores the URL,
> then runs connect → scan → generate metrics → MCP install. No separate `insyte setup`
> command and no `setup.sh` are needed.

Package name: **`insyte`** · command: **`insyte`** · Python **>= 3.11**.

> Before release, confirm the name `insyte` is free on PyPI (https://pypi.org/project/insyte/).
> If taken, fall back to `insyte-analytics` and keep the `insyte` command.

---

## 1. Target onboarding UX (the whole point)

A brand-new user should need exactly this — no shell scripts, no venv juggling, no env vars:

```bash
pip install insyte
insyte init
```

`insyte init` is a single guided flow that:

1. **Checks the environment** (Python version, that install succeeded).
2. **Asks for the database URL** → validates it's read-only-friendly, stores it once in a
   `0600` file (`~/.insyte/projects/<name>/.database_url`) — never in `config.yaml`, never logged.
3. **Validates the connection** (`connect`), then **scans the schema** (`scan`) and
   **generates metrics** (`semantic generate`).
4. **Detects the user's AI tool** (`claude` / `codex` on PATH) and **installs the MCP server**
   into whichever is present (asks only if both). Works with no AI tool too — Studio's
   deterministic parser still answers metric questions.
5. **Prints next steps** and offers to open Studio.

After that, from any terminal:

```bash
insyte studio     # browser workspace
insyte chat       # terminal UI
insyte analyze <metric> --by <dimension>
```

Everything already works per-project because the DB URL is stored and the safety pipeline is
un-bypassable.

---

## 2. Guided `insyte init` — DONE ✅

`insyte init` already ports the `setup.sh` flow into the CLI (`src/insyte/cli/init_command.py`):

- Interactive: prompt name → **read-only DB URL** (stored `0600`) → **AI tool** (claude / codex /
  both / none).
- After creating the project it runs `connect → scan → semantic generate → mcp install` for the
  chosen tool (`_run_guided_setup`), then prints the "ready" panel.
- SSL defaults to `prefer` (least-friction for local + remote); each step is fault-tolerant
  (a failed connect stops the rest with a clear hint; setup never crashes `init`).
- `--yes` / flag mode stays non-interactive and skips the live setup (used by tests/automation);
  `--no-setup` skips the live steps explicitly.
- **No env vars required**: the Codex default includes `--skip-git-repo-check` and the resolver
  falls back Claude→Codex.

Acceptance (met): on a clean machine, `pip install insyte && insyte init` (paste URL, pick tool)
→ `insyte studio` works, with no scripts and no exported environment variables.

---

## 3. Remove the shell scripts and demo/seed files

These exist only because we were pre-PyPI. Once this release is cut, delete them:

- `scripts/setup.sh`  → replaced by `insyte init`
- `scripts/dev-setup.sh` → replace with a short "Contributing" section in the README
  (`uv venv && uv pip install -e '.[dev]'`)
- `scripts/setup_flipkart.sh`, `scripts/seed_flipkart.sql`, `scripts/seed_test_ecommerce.sql`
  → the demo database is not part of the product. Either:
  - drop them entirely, or
  - move the SQL to `examples/` and add an optional `insyte demo` command that seeds a local
    demo DB (nice-to-have, not required for launch).
- Delete the whole `scripts/` directory if nothing remains.

Also update any docs that reference these scripts (see §5, §6).

---

## 4. Packaging & metadata (pyproject.toml)

- `name = "insyte"` (done), bump `version` per release (`0.1.0` → `0.1.0` first publish).
- Fill in real metadata: `description`, `authors`, `license`, `keywords`,
  `[project.urls]` (Homepage, Repository, Issues), and classifiers
  (Python versions, License, "Development Status").
- Confirm the wheel bundles the Studio SPA **and the logo**:
  `[tool.hatch.build.targets.wheel] artifacts = ["src/insyte/studio_dist/assets/**"]`
  already covers `app.js`, `app.css`, `logo.png`, `index.html`.
- Verify runtime deps in `[project.dependencies]` are complete (typer, rich, pydantic,
  sqlalchemy, psycopg, sqlglot, duckdb, textual, fastapi, uvicorn, mcp, …) so a bare
  `pip install insyte` pulls everything — **no `[dev]` needed to run**.
- Keep `requires-python = ">=3.11"`.

---

## 5. Rewrite the README

Rewrite `README.md` around the new one-line install. Sections:

1. **What is Insyte** — one paragraph: local-first AI analytics over your DB, read-only, safe.
2. **Safety guarantees** (keep prominent): AI never sees DB credentials; nothing bypasses SQL
   validation / row limits / PII masking / audit log; Studio binds to `127.0.0.1` only.
3. **Install & set up**:
   ```bash
   pip install insyte
   insyte init           # asks for DB URL + AI tool, does connect/scan/metrics/MCP
   insyte studio         # or: insyte chat
   ```
4. **Requirements** — Python 3.11+, a PostgreSQL database, optionally the `claude` or `codex`
   CLI for natural-language questions (Studio works without one, with metric-style questions).
5. **Using it** — a few example questions ("total sales last month", "revenue by city",
   "what's the expected revenue this year?"), and the surfaces (Studio, chat, `analyze`,
   MCP for Claude/Codex).
6. **Read-only user setup** — the recommended `CREATE ROLE … GRANT SELECT` snippet.
7. **Configuration** — where things live (`~/.insyte/projects/<name>/`), blocked
   tables/columns, `analytics.mode`.
8. **Contributing** — `uv venv && uv pip install -e '.[dev]'`, run `ruff`/`mypy`/`pytest`.
9. **Feedback & support** — see §7.
10. **License**.

Remove all references to `scripts/setup.sh`, `setup_flipkart.sh`, and manual venv rebuilds.

---

## 6. Update the other docs

- `docs/QUICKSTART.md` — collapse to the `pip install insyte` + `insyte init` flow; delete the
  from-checkout / venv-rebuild / `./scripts/setup.sh` instructions.
- `docs/mcp.md` — keep, but note that `insyte init` already installs MCP, and that no
  `--embed-secret` is needed because the URL is stored during setup.
- Delete/relocate anything referencing the seed scripts.

---

## 7. Add a feedback channel

Make it easy for users to report issues and ideas:

- Add **GitHub Issues** link + a short issue template (bug / feature / question).
- In the README "Feedback" section and in `insyte init`'s final message, print:
  *"Found a bug or have an idea? Open an issue: <repo-url>/issues"*.
- Optional: `insyte feedback` command that opens the issues page (or prints the link).

---

## 8. Release checklist (TestPyPI → PyPI)

```bash
# 0. Clean tree, all green
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy src && uv run pytest -q

# 1. Build
uv build                      # produces dist/insyte-<ver>-py3-none-any.whl + .tar.gz

# 2. Verify the wheel contains the SPA + logo
python -m zipfile -l dist/insyte-*.whl | grep studio_dist   # index.html, app.js, app.css, logo.png

# 3. Smoke-test in a CLEAN environment
python -m venv /tmp/insyte-test && /tmp/insyte-test/bin/pip install dist/insyte-*.whl
/tmp/insyte-test/bin/insyte --version
/tmp/insyte-test/bin/insyte init            # against a throwaway DB

# 4. Upload to TestPyPI, install from there, re-smoke-test
uv publish --publish-url https://test.pypi.org/legacy/ dist/*
pip install -i https://test.pypi.org/simple/ insyte

# 5. Publish to PyPI
uv publish dist/*             # needs a PyPI API token

# 6. Tag the release
#    git tag v0.1.0 && git push --tags
```

Post-publish: `pip install insyte` in a fresh venv on a different machine and run the full
onboarding once more.

---

## 9. Acceptance criteria (definition of done)

- [ ] `pip install insyte` in a clean venv pulls all runtime deps; `insyte --version` works.
- [ ] `insyte init` runs the full guided flow (URL → connect → scan → metrics → MCP) with
      **no shell scripts and no environment variables**.
- [ ] `insyte studio` serves the SPA (with the logo) and answers free-form questions via the
      user's Claude/Codex; `insyte chat` works too.
- [ ] `scripts/` is gone; README + QUICKSTART reflect the `pip install` flow.
- [ ] Wheel bundles `studio_dist/assets/**` (SPA + logo).
- [ ] Feedback/issues link is present in the README and in `insyte init` output.
- [ ] Safety guarantees intact: credentials never reach the model or logs; Studio is
      localhost-only; all queries pass SQL validation.

---

## 10. Out of scope for first release (backlog)

Parked capability work, to schedule after launch:

- Ad-hoc filters ("revenue for delivered orders in Mumbai") + AVG/ratio/distinct metrics.
- Saved investigations and richer workspace navigation for investigation results.
- Month-level & seasonal forecasting.
- Response-speed work (expand deterministic parser, fast model for translation, cache).
- Minor polish: "Total MRP" acronym casing, favicon from the icon.
