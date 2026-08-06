# Contributing

Thanks for looking. This is a small, focused tool — issues and PRs welcome.

## Development setup

```bash
git clone <this repo> && cd memo-bank
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q          # 69 tests, ~1s
.venv/bin/memobank --help
```

Python ≥3.11. CI runs the suite on 3.11–3.13.

## The one architectural rule

**The engine carries no project literals.** Everything project-specific lives in
a consuming project's `.island-slices.json` and its corpus — never in this repo.
Concretely:

- No hardcoded paths, project names, spec ids, or vocabulary in engine modules.
- Tests are **fixture- or tmp-based** so they pass against any corpus. A test that
  asserts a particular project's docs belongs in that project, not here.
- Shipped templates (`templates/`) must scaffold a project that validates cleanly
  with zero edits.

If you find a project literal, that's a bug — it has bitten this codebase before
(a validator that walked to the home directory, templates that made every new
project start with errors).

## Layout

```
memo_bank.py            MCP server + federation (the 8 tools)
cli.py                  `memobank` subcommand dispatch
project_config.py       reads .island-slices.json
coverage_loop.py        missing-spec loop      + coverage_backlog.py (its store)
drift_check.py          stale-spec check
doc_validator.py        frontmatter + cross-ref validator
scaffold.py             `memobank init`
benchmark_time_to_context.py
templates/  hooks/  fixtures/
```

Flat modules are deliberate: they stay directly runnable (`python drift_check.py`)
for git hooks, while `py-modules` in `pyproject.toml` makes them importable once
installed. If you add a module, add it there too.

## Pull requests

- Add or update tests for behavior changes; keep the suite green and its output clean.
- Keep changes focused — this codebase favors small, verifiable steps.
- Both maintenance loops are **non-blocking by design** (always exit 0). Please don't
  make them gate commits: a blocking check pressures people into writing stub specs,
  which defeats the point.

## Reporting issues

Include your Python version, how you installed, your `.island-slices.json` (redact
anything private), and the command plus its output.
