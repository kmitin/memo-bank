"""Tests for the memo-bank docs validator (engine-generic: fixture-based).

Run from tools/docs-validator/ with: python3 -m pytest test_validator.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from doc_validator import (  # noqa: E402
    Registry,
    build_registry,
    parse_frontmatter_md,
    validate_doc,
)

FIXTURES = Path(__file__).parent / "fixtures"



# ---------------------------------------------------------------------------
# Fixture-level tests (synthetic edge cases under fixtures/)
# ---------------------------------------------------------------------------

def _validate_fixture(name: str, extra_registry: Registry | None = None) -> list:
    """Parse one fixture file and validate it against a minimal registry."""
    doc = parse_frontmatter_md(FIXTURES / name)
    assert doc is not None
    reg = extra_registry or Registry()
    return validate_doc(doc, reg)


def test_valid_spec_passes():
    issues = _validate_fixture("valid-spec.md")
    errors = [i for i in issues if i.level == "error"]
    assert errors == [], errors


def test_valid_archive_passes_latent_admissibility():
    """Archive with produced: null and applies_to matching zero files is admissible."""
    issues = _validate_fixture("valid-archive.md")
    errors = [i for i in issues if i.level == "error"]
    assert errors == [], errors


def test_missing_required_fields_fails():
    issues = _validate_fixture("invalid-missing-required.md")
    errors = [i for i in issues if i.level == "error"]
    msgs = " ".join(i.msg for i in errors)
    # The fixture is missing these specific required fields; assert each is flagged.
    # (The fixture HAS id, kind, title, indexed — so those won't appear in errors.)
    for required in ["subproject", "status", "owner",
                     "last_reviewed", "applies_to", "tags", "related"]:
        assert required in msgs, f"expected error mentioning '{required}': {msgs}"


def test_archive_indexed_true_fails():
    issues = _validate_fixture("invalid-archive-indexed.md")
    errors = [i for i in issues if i.level == "error"]
    msgs = " ".join(i.msg for i in errors)
    assert "indexed: false" in msgs or "must have indexed: false" in msgs, msgs


def test_dangling_ref_fails():
    issues = _validate_fixture("invalid-dangling-ref.md")
    errors = [i for i in issues if i.level == "error"]
    msgs = " ".join(i.msg for i in errors)
    assert "this-spec-does-not-exist-anywhere" in msgs, msgs
    assert "also-not-real" in msgs, msgs


# Live-corpus tests (golden archive fixture, whole-corpus registry build, schema
# self-validation) are INSTANCE concerns — they assert a particular project's
# corpus, so they live with that project, not in the engine.





# ---------------------------------------------------------------------------
# Index generation — generic: built over a tmp corpus, not any live project
# ---------------------------------------------------------------------------

def _tmp_corpus(tmp_path):
    """A minimal one-doc corpus, built from the valid-spec fixture."""
    specs = tmp_path / "docs" / "specs"
    specs.mkdir(parents=True)
    (specs / "valid-spec.md").write_text((FIXTURES / "valid-spec.md").read_text())
    return tmp_path


def test_build_index_shape(tmp_path):
    """The generated index has the expected top-level shape and doc entries."""
    from doc_validator import build_index, build_registry

    root = _tmp_corpus(tmp_path)
    reg, _ = build_registry(root)
    index = build_index(root, reg)

    for key in ("version", "schema", "generated_at", "stats", "docs", "haft_carriers"):
        assert key in index, f"missing top-level key: {key}"
    assert index["version"] == 1
    assert index["schema"] == "schema-frontmatter-v1"
    assert index["stats"]["docs_count"] == len(index["docs"])
    assert index["stats"]["haft_count"] == len(index["haft_carriers"])
    for entry in index["docs"]:
        for field_name in ("id", "kind", "subproject", "title", "path",
                           "applies_to", "tags", "related", "indexed"):
            assert field_name in entry, f"index entry missing {field_name}: {entry}"


def test_index_is_json_serializable(tmp_path):
    """The index must round-trip through JSON (dates coerced to strings)."""
    import json

    from doc_validator import build_index, build_registry

    root = _tmp_corpus(tmp_path)
    reg, _ = build_registry(root)
    assert json.loads(json.dumps(build_index(root, reg)))["version"] == 1
