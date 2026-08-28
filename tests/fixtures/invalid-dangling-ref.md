---
id: invalid-dangling-ref
kind: spec
subproject: umbrella
title: Dangling cross-reference
status: active
owner: test
last_reviewed: 2026-05-18
valid_until: 2026-08-18
applies_to:
  - tools/docs-validator/fixtures/*.md
tags:
  - fixture
related:
  - spec:this-spec-does-not-exist-anywhere
  - note:also-not-real
indexed: true
---

# Invalid: dangling cross-references

Both `related:` entries point to ids that don't exist in the corpus. Must fail per cross_ref_resolution.
