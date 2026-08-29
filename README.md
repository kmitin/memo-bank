# memo-bank

**Your specs are contracts. This makes an agent read them before it edits your code — and tells you when they rot.**

memo-bank is a read-only [MCP](https://modelcontextprotocol.io) server over a
git-native markdown corpus, plus two maintenance loops that keep that corpus
honest. Point it at a repo and an agent can answer *"what rules govern this
file?"* in about two reads, instead of re-deriving the answer from forty files
every time.

MIT licensed · Python ≥3.11 · three dependencies (`mcp`, `python-frontmatter`, `PyYAML`).

## Why

Documentation rots in two distinct ways, and most tooling addresses neither:

- **Missing** — code exists that no doc governs. → the **coverage loop** surfaces
  uncovered code *that is actually being edited* as a ranked "spec-wanted" backlog.
- **Stale** — a doc exists but the code moved on. → the **drift check** flags any
  governing doc whose governed files changed after its `last_reviewed`.

Both run non-blocking on pre-commit. Neither invents content: they tell you what
to write and when to revisit, and the corpus stays plain markdown in git.

## Install

```bash
pip install -e '.[dev]'      # from a clone; PyPI publishing not set up yet
memobank --help
```

## Use

Adopting the memo-bank in a new project? See **[SCAFFOLDING.md](SCAFFOLDING.md)**.

```bash
memobank init --target ../my-project --island my-project --slice umbrella=.
memobank validate ../my-project --index docs/index.json
memobank serve    --federation ../my-project/.island-slices.json   # the MCP server
memobank coverage --mode staged                                    # missing specs
memobank drift    --registry .island-slices.json                   # stale specs
memobank benchmark --federation .island-slices.json                # time-to-context
```

`init` writes only what the project owns — `.island-slices.json`, `AGENTS.md`,
the corpus skeleton, and the authoring templates. **No engine code is copied**,
so a project can never carry a forked engine that ages out of sync.

## See it work

You're about to edit a file. Ask what governs it:

```
$ memobank serve … →  docs.resolve_path("src/services/api.ts")

  hmac-signing-client   (matched glob: src/services/api.ts)
  → docs.get("hmac-signing-client") → the contract you must satisfy:
      "NEVER log the server token, even partially."
      "NEVER sign a path that differs from what the server receives."
```

Two reads, and the rule that would have bitten you is in hand. Ask about a *topic*
instead, and expansion is what makes lexical search land:

```
docs.search_live("crawling reviews")                      →  top hit, score  3.0
docs.search_live("refresh fetch ingest cache stale quota") →  top hit, score 32.0
```

Same corpus, same intent — the second query uses the words the docs actually use.

Then the loops keep it honest:

```
$ memobank coverage --mode staged
⚠ 1 changed file(s) have no governing spec — added to the spec-wanted backlog:
  - src/services/audio.ts

$ memobank drift --registry .island-slices.json
⚠ 1 governing doc(s) may be stale — governed code changed since their last_reviewed:
  - review-ingestion-status (last_reviewed 2026-06-27) — 7 changed: …
```

## The agent skill

`skills/memo-bank-query/` is a Claude Code skill that teaches an agent *when and
how* to query the corpus — resolve-before-edit, expand topic queries with domain
synonyms, and treat a miss as "undocumented", not "unconstrained". Install it once
and it applies to every project you work in:

```bash
cp -r skills/memo-bank-query ~/.claude/skills/     # user-global
# or, per project:  cp -r skills/memo-bank-query <project>/.claude/skills/
```

The MCP tools work without it; the skill is what stops an agent from grepping docs
by hand or inventing a rule when none exists.

## The corpus model

Each *slice* (a repo, or a subproject within one) owns
`docs/{specs,state,archive}/`:

| kind | meaning | indexed |
|---|---|---|
| `spec` | a present-tense contract — "what must hold" | yes (hot) |
| `state` | a current snapshot — "what the situation is now" | yes (hot) |
| `archive` | cold history — "what we used to do and why it changed" | no |

Frontmatter is a validated schema; `applies_to` globs are the precedence surface
(closest glob wins), and cross-references are stable `kind:id` handles rather
than paths. Specs are written **implementation-independent** — five sections
(Problem · Contract · Restrictions · Open threads · Code references), with
concrete file references confined to the last one, so the contract survives
refactors.

## The tools (MCP surface)

`docs.list` · `docs.get` · `docs.get_section` · `docs.resolve_path` ·
`docs.search_live` · `docs.search_archive` · `docs.resolve_term` ·
`docs.compose_context`

They form an **incremental-load ladder**: pointers → one section → one doc →
ranked search → a budget-bounded bundle. Retrieval is lexical (bag-of-words, no
embeddings, no vendor lock) — so expand a topic query with domain synonyms
before searching; `docs.search_live`'s own description says so, and it roughly
10×'d top-hit scores in practice.

`docs.resolve_term` reads project vocabulary through **spec-source adapters**
(below); with no source present it reports `absent` rather than failing. The
engine depends on no other tool.

## Spec-source adapters

Most projects already keep their vocabulary and decisions in whatever methodology
they use. The engine hardcodes no list of locations — each ecosystem is an
**adapter** that declares *where its artifacts live*, and everything downstream
(terms, and the drift check) works off that.

Built in:

| adapter | artifacts it contributes |
|---|---|
| `haft` | `.haft/specs/term-map.md` — bounded-context vocabulary |
| `docs-native` | `docs/_terms/term-map.md` — the memo-bank's own location |
| [`grill-with-docs`](https://github.com/mattpocock/skills) | `CONTEXT.md` glossary (root or per-context) + `docs/adr/` decisions |
| [`superpowers`](https://github.com/obra/superpowers) | `docs/superpowers/specs/` designs + `docs/superpowers/plans/` |

**Why this matters beyond terms:** the drift check needs to know which document
governs which code. memo-bank's own specs declare that in frontmatter
(`applies_to` + `last_reviewed`). Foreign artifacts — an ADR, a design doc —
declare neither. So an adapter reports the **file paths the document mentions**
as its governed set, and the document's own last commit date stands in for the
review date. The result: `memobank drift` flags a stale ADR or design in any
registered ecosystem, not just memo-bank specs.

**Adding an ecosystem is a new adapter, never an engine edit.** A markdown
glossary in a fenced ```yaml term-map block is four lines:

```python
from pathlib import Path
from spec_sources import FencedTermMapSource, register_source

class MyMethodSource(FencedTermMapSource):
    name = "my-method"
    rel_paths = (Path("my-method") / "glossary.md",)

register_source(MyMethodSource())      # register_source(..., first=True) to win precedence
```

A different shape subclasses `SpecSource` and implements `detect(root)`, plus
`read_terms(root)` and/or `artifacts(root)` — see `spec_sources.py` and the
tests for worked examples. Sources are merged; registration order breaks ties.
Adapters for other methodologies are welcome; that is the intended way to grow
support.

## Configuration

One file, `.island-slices.json`, is the whole adoption contract:

```json
{
  "island": "my-project",
  "slices": [{ "name": "umbrella", "root": "." },
             { "name": "api", "root": "services/api" }],
  "source_globs": ["src/**"],
  "schema": "docs/specs/schema-frontmatter-v1.md"
}
```

Only `slices` is required; everything else defaults. The engine carries no
project literals.

## Status

Working software, used on real projects — not a polished product. Known rough
edges: the `island`/`slices` vocabulary is inherited from the first project that
used it; `memobank init` doesn't install the git hook (copy `hooks/pre-commit`
yourself); `last_reviewed` is date-granular, so same-day edits after a refresh
re-flag; `mcp` is pinned `<2` (2.x changes the `Server` API — untested).

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
