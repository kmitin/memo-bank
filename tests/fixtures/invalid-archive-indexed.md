---
id: invalid-archive-indexed
kind: archive
subproject: umbrella
title: Archive entry incorrectly flagged indexed:true
status: landed
owner: test
last_reviewed: 2026-05-18
feature: validator-test
date: 2026-05-18
produced: null
superseded_by: null
applies_to: []
tags: []
related: []
indexed: true
---

# Invalid: archive must NOT be indexed

Archive entries are RAG-cold by definition. indexed:true on archive must fail (hot/cold split invariant).
