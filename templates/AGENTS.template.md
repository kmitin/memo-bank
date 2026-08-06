<!--
AGENTS.md TEMPLATE — copy to the project root as AGENTS.md and fill the REPLACE
blocks. The two "loop" sections (load context / drift check) are framework-
universal: keep them as-is. Lives under docs/_templates/ (validator-skipped), so
it is not a corpus document. Do NOT copy a reference project's filled AGENTS.md —
instantiate this template instead.
-->
# AGENTS.md — REPLACE-project-name

Entry point for agents working in this repo. Read this first; it's a map, not a manual.

REPLACE: one or two sentences — what this project is, and whether it's a single
repo or an island of repos.

| slice | path | what it is |
|---|---|---|
| REPLACE | REPLACE (repo-relative root) | REPLACE |

## START HERE — load context before you edit

Before editing or reasoning about any file, **load the spec that governs it** from
the **memo-bank** — contracts are documented and code follows them. A governing
spec may forbid exactly what you're about to do.

- Invoke the `memo-bank-query` skill (if installed), or
- call `docs.resolve_path(<repo-relative path>)` → `docs.get(<id>)` directly (the
  memo-bank MCP server is registered in `.mcp.json`).

Querying notes: search is **bag-of-words with no substring match** (`auth` ≠
`authentication`) — **expand the query with domain synonyms first**. An empty
`resolve_path` result means the path is *undocumented*, not unconstrained — say so;
don't fabricate a rule.

## Keep docs in sync — drift check (run this in your loop)

Loading context before editing is half the contract; the other half is keeping the
governing docs current *as code changes*. Before committing a batch of work — or
whenever a governed area changed substantially — run a quick drift check:

1. **See what changed:** `git diff --name-only` (and `--cached`).
2. **Find each changed file's governing doc:** `docs.resolve_path(<path>)`.
3. **If a governed file changed *significantly*** — a couple of files under one
   spec's `applies_to`, or a large diff — **and the governing doc's `last_reviewed`
   predates the change, the doc is probably stale.** Suggest a **memo-bank refresh**.

A "memo-bank refresh" of a doc: re-read it against the changed code; for a `spec`
update the drifted contract (implementation-independent — five sections, code refs
only at the bottom); for a `state` doc re-snapshot to current reality and move
historical content to `docs/archive/`; bump `last_reviewed` (and `valid_until`);
commit the doc update **with** the code change. If a haft decision/ADR governs the
same area, run its haft refresh/verify loop too. **`make drift-check` automates
steps 1–3** (and the pre-commit hook runs it). Non-blocking — a heads-up, not a gate.

## Governing specs by area

REPLACE: list the live specs grouped by area/slice, e.g.
- **REPLACE-slice** — `spec-id` (what it governs), `spec-id` (…)

## Tooling (from the repo root)

```bash
make validate        # frontmatter + cross-ref validator
make test            # docs-validator + memo-bank suites
make coverage-loop   # demand-driven coverage loop (non-blocking)
make install-hooks   # install the coverage-loop pre-commit hook into every repo
make index           # regenerate docs/index.json
```

The coverage loop runs on commit (hooks): editing source under `source_globs` with
no governing spec surfaces it in a ranked spec-wanted backlog. `coverage_loop.py
--render` shows it.

## Conventions

- **Specs are implementation-independent contracts.** Author against
  `docs/_templates/spec-template.md` — five fixed sections (Problem · Contract ·
  Restrictions · Open threads · Code references); concrete refs only in the last.
- Corpus is V2-nested: each slice owns `docs/{specs,state,archive}/` (specs/state
  hot, archive cold). Cross-refs are stable `kind:id` handles.
- REPLACE: any project-specific shell/command conventions (see this project's CLAUDE.md).

## Governance

REPLACE (if the project uses haft): decisions live in a `.haft` reasoning graph;
run `haft_query(action="status")` at session start; core edits expect a recorded
decision/ADR.

## Deep state

- REPLACE: pointer to the project's current-state / handoff doc, if any.
- Reusing the framework elsewhere: `docs/_design/memo-bank-adoption.md`.
