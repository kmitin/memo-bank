# Scaffolding a project — handoff

How to adopt the memo-bank in a new repo. The engine is **installed, never
vendored**: the project ends up holding only its own config, corpus and templates.

## The four commands

```bash
cd <project>
git init                                    # both loops need git

python3 -m venv .venv
.venv/bin/pip install -e ~/Projects/memo-bank

.venv/bin/memobank init --target . --island <name> --slice umbrella=. \
    --source-glob 'src/**'                  # repeat --slice / --source-glob as needed

.venv/bin/memobank validate . --index docs/index.json
```

Then install the hook (see *Known gaps*):
```bash
cp ~/Projects/memo-bank/hooks/pre-commit .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit
```

## The three decisions

| decision | guidance |
|---|---|
| `--island <name>` | the project name; cosmetic |
| `--slice name=root` | one per docs corpus. Single repo → `umbrella=.`. Multi-repo/monorepo → one per subproject (e.g. `api=services/api`) |
| `--source-glob` | what the **coverage loop** treats as "source needing a governing spec". Code → `src/**`. Content/writing → `content/**`, `posts/**`. Unsure → take the default and change the one line later |

## What the project gets — and doesn't

```
.island-slices.json                   config (the whole adoption contract)
AGENTS.md                             agent entry point; both loops baked in
Makefile                              targets calling the installed `memobank`
.mcp.json                             merged — other servers preserved
docs/{specs,state,archive}/           corpus skeleton, per slice
docs/_templates/spec-template.md      authoring template
docs/specs/schema-frontmatter-v1.md   the canonical schema (now the project's own)
```

**Zero engine files.** Nothing to fork, nothing to age out of sync.

## Verify

```bash
.venv/bin/memobank validate . --index docs/index.json   # expect 0 errors
.venv/bin/memobank drift    --registry .island-slices.json
.venv/bin/memobank coverage --mode staged --registry .island-slices.json
```
A fresh scaffold validates clean and both loops run non-blocking (always exit 0).

Add to `.gitignore`: `.venv/`, `.memobank-backlog.json`.

## Then

Author the first spec in `docs/specs/` against the template — five sections
(Problem · Contract · Restrictions · Open threads · Code references),
implementation-independent, concrete refs only in the last one. The coverage loop
will tell you what else wants a spec as you edit; the drift check will tell you
when one has gone stale.

Querying is a **global skill** (`~/.claude/skills/memo-bank-query/`) — available in
every project, no per-project setup.

## Known gaps

- **`memobank init` does not install the pre-commit hook** — do it manually (above),
  or the loops never run on commit.
- **MCP needs a session restart** — `.mcp.json` is read at startup.
- `mcp` is pinned `<2` (2.x changes the `Server` API; untested).

## Migrating a project that vendored an older engine

Install the engine, repoint `.mcp.json` / Makefile / hook at `memobank`, move
instance files out (corpus tests → `tests/`, eval data → `eval/`), delete the
vendored engine directory, then verify as above.
