<!--
================================================================================
SPEC AUTHORING TEMPLATE  (kind: spec — a present-tense contract: "what must hold")
================================================================================
Copy this file to the target subproject's docs/specs/<id>.md, fill in the
frontmatter and body, delete these HTML-comment instructions, set status: active.

Lives under docs/_templates/ which the validator SKIPS (underscore-prefixed
segment) — so this template is not itself a corpus document and never appears
in docs.list / resolve_path.

WHICH KIND AM I WRITING?
  spec     present-tense contract — "what must hold / how it works"   ← this template
  state    current snapshot — "what the situation is right now"
  archive  cold history — "what we used to do and why it changed" (indexed: false)
  (decision/vision are NOT kinds in this schema)

CONTRACT-FIRST RULE (mandatory — applies to EVERY spec):
  A spec is the gold-ground contract: it defines the functionality / configuration,
  and the CODE follows the spec, not the reverse. Therefore the body MUST be
  implementation-independent:
    - NO class names, method names, or line numbers in the body.
    - NO framework-internal types or annotation names as the contract.
    - Describe interfaces, flows, and behaviour ABSTRACTLY. External/behavioural
      contracts (an endpoint shape, an embed-URL form, a print dimension) are fine;
      internal class/method coupling is not.
    - ALL concrete pointers (files, classes, related specs, origin decisions/notes)
      go ONLY in the "## Code references" section at the bottom.

CANONICAL SECTION STRUCTURE (fixed — use EXACTLY these five H2 sections, in this
order, in every spec; no other top-level sections):

  ## Problem
      The problem this contract guards and why it is a contract, not a suggestion.
  ## Contract
      Numbered, behavioural, implementation-independent statements — the gold
      ground. Interfaces / flows / examples go HERE, inline (a bullet list, a
      numbered step list, or a fenced sketch), described abstractly.
  ## Restrictions (admissibility)
      What VIOLATES the contract — "NEVER ..." bullets.
  ## Open threads
      Known gaps, deferred enforcement, conditions that would change this contract.
  ## Code references
      The ONLY place for concrete pointers: files/dirs, related specs (kind:id),
      cross-repo pointers, origin decision/note ids.

REQUIRED frontmatter fields (schema-frontmatter-v1, frozen):
  id           stable kebab-case slug; NEVER renamed; the cross-ref handle
  kind         spec
  subproject   umbrella | server | frontend | game-app
  title        one line, what this governs
  owner        a person, not "the team"
  status       active   (a shipped contract; use sparingly until the rule is real)
  last_reviewed  YYYY-MM-DD
  applies_to   glob list, RELATIVE TO THIS SUBPROJECT'S ROOT — the closest-wins
               precedence surface. Make it as NARROW as the rule truly governs;
               broad globs (e.g. src/**) cause cross-slice ties. Avoid docs/**/*.md.
  tags         keyword list for faceted retrieval (auth, hmac, playback, ...)
  related      ID cross-refs that MUST resolve 100% within THIS repo's corpus
               (cross-repo refs go in ## Code references prose, not here)
  indexed      true   (specs are hot / RAG-indexed)
OPTIONAL:
  sources      source doc(s) this spec was distilled from
  supersedes   id of a prior spec this replaces
================================================================================
-->
---
id: REPLACE-with-kebab-slug
kind: spec
subproject: REPLACE
title: REPLACE — one-line statement of what must hold
owner: REPLACE
status: active
last_reviewed: REPLACE-YYYY-MM-DD
valid_until: REPLACE-YYYY-MM-DD
applies_to:
  - REPLACE/narrow/glob/**
tags:
  - REPLACE
related: []
indexed: true
---

# REPLACE — spec title

## Problem

<!-- What problem this contract guards; why it is a contract, not a suggestion.
     Present tense. No implementation. -->

## Contract

<!-- Numbered, behavioural, implementation-independent statements — the gold
     ground. Put interfaces / flows / examples HERE, abstractly. -->

1. REPLACE
2. REPLACE

## Restrictions (admissibility)

<!-- What VIOLATES the contract. Mirror the mistake this spec prevents. -->

- NEVER REPLACE

## Open threads

<!-- Known gaps, deferred enforcement, conditions that would change this contract. -->

## Code references

<!-- The ONLY place for concrete pointers. e.g.:
       - Implementation: `path/or/package/`
       - Related: `spec:other-id` (same corpus); cross-repo: `spec:x` in `other-repo`
       - Origin: `note:note-...`, `dec:dec-...` -->
