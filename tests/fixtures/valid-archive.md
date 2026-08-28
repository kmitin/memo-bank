---
id: valid-archive-fixture
kind: archive
subproject: umbrella
title: Valid archive fixture (latent — no produced spec yet)
status: landed
owner: test
last_reviewed: 2026-05-18
feature: validator-test
date: 2026-05-18
produced: null
superseded_by: null
applies_to:
  - some/deleted/code/that/no/longer/exists/**
tags:
  - fixture
  - latent
related: []
indexed: false
---

# Valid archive fixture (latent)

Tests two things at once:
1. Archive entry with `produced: null` is admissible (latent_archive_admissibility).
2. Archive entry with `applies_to` matching zero files is admissible (applies_to_archive_exemption).
