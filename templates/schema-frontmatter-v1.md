---
id: schema-frontmatter-v1
kind: spec
subproject: umbrella
title: Docs corpus — frontmatter schema v1
status: active
owner: TODO
last_reviewed: 2026-06-29
applies_to:
  - docs/**/*.md
tags:
  - schema
  - validator
  - frontmatter
  - corpus
  - contract
related: []
indexed: true
---

# Frontmatter schema v1

The contract every doc in the corpus MUST satisfy. The validator (pre-commit hook + CI step) refuses to merge any doc that violates this schema. The validator never warns — it either passes silently or fails the build.

This document validates against itself. If the validator can't accept this file, the validator is wrong.

## Field families

Four field families:

- **Identity**: `id`, `kind`, `subproject`
- **Governance**: `title`, `owner`, `last_reviewed`, `status`, `valid_until`
- **Retrieval**: `applies_to`, `tags`, `related`, `indexed`
- **Content**: the markdown body (not in frontmatter)

Kind-specific extensions append additional fields (see § kind-specific extensions).

## Required fields, all docs

| Field | Type | Notes |
|---|---|---|
| `id` | string slug | Stable across renames. Filename is human-affordance only; `id` is identity. Must be unique across the entire corpus per island. Lowercase, kebab-case, no path separators. |
| `kind` | enum | One of `spec`, `state`, `archive`, `term`. |
| `subproject` | enum | Per-project enum. Declare your own allowed values here (e.g. `umbrella`, `api`, `web`) — one per slice in `.island-slices.json`. |
| `title` | string | Human-readable. No length cap, but a one-line summary is preferred. |
| `owner` | string | Author / responsible person. Free-form text; the schema does not require this to be a registered identity. |
| `status` | enum | Per-kind allowed values — see § status values per kind. |
| `last_reviewed` | RFC3339 date | The date a human last verified this doc against current code/system state. Used by the quarterly drift audit. |
| `valid_until` | RFC3339 date | After this date the doc is considered stale and surfaces in drift reports. The validator does NOT fail on staleness — staleness is an observation, not a gate. |
| `applies_to` | list of glob | Paths this doc governs. For `kind: spec | state`: must match ≥1 file. For `kind: archive`: exempt from the reachability check (referenced code may be deleted as part of the work the archive documents). |
| `tags` | list of string | Keyword list for retrieval. Free-form initially; the validator soft-warns if a tag is novel relative to a tag-glossary file once one exists. |
| `related` | list of ref | Cross-references to other docs. Format: `<kind>:<id>` (e.g. `spec:auth-protocol`, `state:rollout-status`, `archive:legacy-uploader`, `note:domain-model`). Every entry MUST resolve to an existing doc; 100% resolution is enforced. Empty list is valid. |
| `indexed` | boolean | `true` for `kind: spec | state`; `false` for `kind: archive`. RAG indexers MUST respect this AND the path-rule exclusion of `docs/archive/`. Belt-and-suspenders: the path rule and the flag must agree. |

## Status values per kind

| `kind` | Allowed `status` |
|---|---|
| `spec` | `draft`, `active`, `superseded`, `deprecated` |
| `state` | `draft`, `active`, `superseded`, `deprecated` |
| `archive` | `landed`, `superseded`, `abandoned` |
| `term` | `draft`, `active`, `deprecated` |

Lifecycle:
- `draft` — pending operator approval; not yet load-bearing
- `active` — load-bearing; agents and humans may rely on it
- `superseded` — replaced by another doc (`supersedes` / `superseded_by` chain)
- `deprecated` — no longer applicable; retained for history but not load-bearing
- `landed` (archive only) — the work documented in this archive entry landed in code/system
- `abandoned` (archive only) — the work documented in this archive entry was decided against; the archive captures why

## Kind-specific extensions

### `kind: spec` (stable contracts)

No additional required fields beyond the common set. Optional:

- `supersedes: <kind>:<id>` — points to the spec or state this one replaces

### `kind: state` (current snapshots)

- `supersedes: <kind>:<id>` (optional) — predecessor state file in a temporal chain

### `kind: archive` (compacted historical context)

- `feature: <slug>` (required) — the feature or work-stream this archive entry documents; archive entries about the same feature live under `docs/archive/<feature>/`
- `date: RFC3339` (required) — when the documented work landed (or was abandoned)
- `produced: <kind>:<id> | null` (required, nullable) — the live spec this archive entry produced as an ancestor. `null` is valid for **latent** archive entries authored before any corresponding spec exists. Latent archive entries are admissible.
- `superseded_by: <kind>:<id> | null` (required, nullable) — points forward to a later archive entry that replaces this one (e.g., approach X tried, abandoned, switched to Y).
- `sources: list of path` (optional) — pre-migration source documents this archive was compacted from.

### `kind: term` (glossary entries)

Terms typically live inline in a single `term-map.md` carrier rather than per-file. When inline, each entry follows the shape demonstrated in `.haft/specs/term-map.md`:

- `term: <slug>` — the term
- `domain: target-system | enabling-system | <other>` — which system the term belongs to
- `definition: |` — multi-line definition

## Cross-reference rules

**Frontmatter `related:`** — strict resolution:
- Every entry MUST be of the form `<kind>:<id>` where `<kind>` is one of `spec`, `state`, `archive`, `term`, `note`, `prob`, `dec`.
- Every entry MUST resolve to an existing doc in the corpus.
- The validator fails the build on any dangling reference. 

**Body text** — informational only:
- Cross-references in prose (e.g. "candidate for a future `spec:playback-pattern`") are NOT validated for resolution.
- The validator MAY warn on unresolved body-text references but MUST NOT fail. Strict resolution applies to frontmatter only.
- This is what enables the archive-informs-spec workflow: an archive entry can name a future spec that doesn't exist yet.

## Validator contract

The pre-commit hook + CI step MUST fail the build on any of these conditions:

1. Missing required field for the declared `kind`
2. `id` duplicated anywhere in the corpus
3. `id` not a valid slug (lowercase, kebab-case, no path separators)
4. `kind` not in the enum
5. `status` not in the allowed set for the declared `kind`
6. `subproject` not in the island's declared enum
7. Frontmatter `related:` entry with invalid format or that doesn't resolve to an existing doc
8. `applies_to` glob matching zero files when `kind in {spec, state}`
9. `indexed: false` declared on `kind in {spec, state}` (these MUST be hot)
10. `indexed: true` declared on `kind: archive` (these MUST be cold)
11. Archive entry missing `feature`, `date`, `produced`, or `superseded_by`

The validator MAY emit warnings (non-blocking) on:

- `last_reviewed` older than `valid_until - 30d` (drift surface)
- Body-text cross-references that don't resolve
- Novel `tags` not seen elsewhere in the corpus
- Archive entries `landed` >90 days ago with no `produced` link (potential latent-to-live promotion candidate)

## Schema versioning

This document is `id: schema-frontmatter-v1`. A future v2 (e.g. introducing a new required field) is `id: schema-frontmatter-v2`, and v1 carries `superseded_by: spec:schema-frontmatter-v2`. The validator reads the latest active `kind: spec, subproject: umbrella` doc with id matching `schema-frontmatter-v*` to determine the contract; migration of existing corpus docs is required before v2 becomes active.

## Self-validation example

The frontmatter at the top of this document satisfies its own schema:

- Required identity / governance / retrieval / content fields: present
- `kind: spec` with `status: active` and `indexed: true`: valid combination
- `applies_to` glob `docs/**/*.md` matches existing files (this file alone)
- `related:` is empty here; when populated, every entry must resolve to a doc in this corpus
- Tags are descriptive

If the validator implementation, when it lands, can parse this file without errors and accept it as `kind: spec`, the validator is correct on the spec-doc path.

## Pointers

- Term map carrier (optional): `docs/_terms/term-map.md`
