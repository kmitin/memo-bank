---
name: memo-bank-query
description: >-
  Load the governing spec/contract for a file or topic from a project's memo-bank
  (a read-only MCP docs corpus) BEFORE reading code or editing. Use this in any
  repo that has a .island-slices.json or a memo-bank MCP server, whenever you are
  about to edit a file, or are asked "what governs X", "is there a spec for Y",
  "what's the contract for Z", "how does this work here", or before changing
  unfamiliar code. Prefer it over grepping docs by hand: it resolves the
  precedence-ordered governing doc in ~2 reads. Do NOT skip it because a change
  "looks small" — a governing spec may forbid exactly what you're about to do.
---

# Querying a memo-bank

A memo-bank is a **read-only** corpus of project docs served over MCP. It answers
"what rules govern this code?" precisely, so you load the contract *before* you
touch the code rather than after you've broken it.

In these projects **specs are contracts and code follows them**. If a spec governs
the path you're editing, satisfy it; if you're about to violate it, stop and say so.

## Is a memo-bank available here?

Look for `.island-slices.json` at the repo root, or `mcp__memo-bank__docs_*` tools.
If neither exists, this skill doesn't apply — say so rather than inventing rules.

Two ways to reach it:
- **MCP tools** — `mcp__memo-bank__docs_*` (preferred when present).
- **The installed CLI** — `memobank serve|validate|drift|coverage` (often at
  `.venv/bin/memobank`); for one-off queries the Python API works too:
  `memo_bank.load_federation(memo_bank.parse_registry(Path(".island-slices.json")))`
  then `fed_resolve_path(fed, path)` / `fed_search_live(fed, query)`.

## Pick the entry point by what you have

### Editing a specific file → `docs_resolve_path` (the common case, ~2 reads)
1. `docs_resolve_path(<repo-relative path>)` → governing doc id(s), ordered by glob
   specificity (closest `applies_to` wins).
2. `docs_get(<top id>)` for the full contract, or `docs_get_section(<id>, <section>)`
   for one part (e.g. the restrictions).
3. **Empty result = a coverage gap** — that path is *undocumented*, not
   unconstrained. Say so plainly; don't fabricate a rule. The project's coverage
   loop will log it as spec-wanted.

### A concept or question → expand first, then `docs_search_live`
Scoring is **lexical bag-of-words**: order and duplicates don't matter, and there
is **no substring match** (`auth` does NOT match `authentication`). So the query
must contain the corpus's actual words.
1. **Expand with domain synonyms before searching.** Add the concrete terms the
   docs plausibly use — e.g. for "login" → `authentication auth login session token
   signature`; for "crawling reviews" → `refresh fetch ingest cache stale quota`.
   This is the single biggest lever on hit quality (it has moved a top hit from a
   score of 3 to 32 in practice).
2. `docs_search_live(<expanded query>)` → ranked pointers; then `docs_get` the top hits.
3. Unsure of the vocabulary? `docs_list` first and reuse its tags.

### Need a bounded bundle → `docs_compose_context`
`docs_compose_context(question=… | path=… | tags=[…], budget_tokens=N)` assembles a
budget-bounded set of sections. Use it when one doc isn't enough but the whole
corpus is too much.

### Other tools
- `docs_list(subproject?, kind?)` — survey what exists.
- `docs_search_archive(query)` — cold history: what we used to do and why it changed.
- `docs_resolve_term(term)` — ground an ambiguous term in this project's vocabulary
  before assuming what it means.

## Restrictions

- **Read-only.** The memo-bank never writes docs; authoring a spec is a separate,
  deliberate act (use the project's `docs/_templates/spec-template.md`).
- **Don't fabricate coverage.** A miss means undocumented — report the gap.
- **Trust precedence.** `resolve_path` already ranks by specificity; read the top
  hit before broadening to search.
- **Specs are implementation-independent.** If you author or update one, keep the
  contract abstract and put concrete file/class references only in its final
  "Code references" section.

## Keeping the corpus honest

Two non-blocking loops usually run on commit — `memobank coverage` (code being
edited that no spec governs) and `memobank drift` (governing docs whose code
changed since their `last_reviewed`). If you make a substantial change to a
governed area, check drift and refresh the doc alongside the code.
