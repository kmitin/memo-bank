"""Spec-source adapters: a new ecosystem must be a new ADAPTER, never an edit
to engine internals."""
from pathlib import Path

import pytest

import spec_sources as ss

TERM_MAP = """```yaml term-map
entries:
  - term: widget-token
    domain: target-system
    definition: |
      The opaque handle a widget presents.
```
"""


def _write(root: Path, rel: str, body: str = TERM_MAP) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


@pytest.fixture(autouse=True)
def _restore_registry():
    """Registry is module-level; keep tests isolated."""
    original = ss.sources()
    yield
    ss._SOURCES[:] = original


def test_builtin_adapters_are_registered_in_precedence_order():
    assert [s.name for s in ss.sources()] == ["haft", "docs-native"]


def test_haft_adapter_detects_and_reads(tmp_path):
    _write(tmp_path, ".haft/specs/term-map.md")
    assert ss.detect_all(tmp_path) == [("haft", tmp_path / ".haft/specs/term-map.md")]
    terms = ss.load_terms(tmp_path)
    assert [t["term"] for t in terms] == ["widget-token"]
    assert terms[0]["source"] == "haft"


def test_docs_native_adapter_works_without_haft(tmp_path):
    _write(tmp_path, "docs/_terms/term-map.md")
    assert not (tmp_path / ".haft").exists()
    assert [t["source"] for t in ss.load_terms(tmp_path)] == ["docs-native"]


def test_multiple_sources_merge_with_registration_precedence(tmp_path):
    _write(tmp_path, ".haft/specs/term-map.md")
    _write(tmp_path, "docs/_terms/term-map.md",
           TERM_MAP.replace("target-system", "other-domain"))
    terms = {t["term"]: t for t in ss.load_terms(tmp_path)}
    assert len(terms) == 1                          # same term, deduped
    assert terms["widget-token"]["source"] == "haft"  # earlier adapter wins
    assert len(ss.artifact_paths(tmp_path)) == 2    # both tracked for reload


def test_no_source_yields_no_terms(tmp_path):
    assert ss.load_terms(tmp_path) == []
    assert ss.detect_all(tmp_path) == []


def test_adding_an_ecosystem_is_just_a_new_adapter(tmp_path):
    """The extension contract: subclass + register, no engine edit."""
    class MyMethodSource(ss.FencedTermMapSource):
        name = "my-method"
        rel_paths = (Path("my-method") / "glossary.md",)

    _write(tmp_path, "my-method/glossary.md")
    assert ss.load_terms(tmp_path) == []            # not registered yet

    ss.register_source(MyMethodSource())
    terms = ss.load_terms(tmp_path)
    assert [t["source"] for t in terms] == ["my-method"]
    assert ("my-method", tmp_path / "my-method/glossary.md") in ss.detect_all(tmp_path)


def test_register_first_takes_precedence(tmp_path):
    class OverrideSource(ss.FencedTermMapSource):
        name = "override"
        rel_paths = (Path("override.md"),)

    _write(tmp_path, ".haft/specs/term-map.md")
    _write(tmp_path, "override.md")
    ss.register_source(OverrideSource(), first=True)
    assert ss.load_terms(tmp_path)[0]["source"] == "override"


def test_custom_parsing_adapter_can_bypass_the_fenced_format(tmp_path):
    """An ecosystem with a different artifact shape implements SpecSource directly."""
    class PlainListSource(ss.SpecSource):
        name = "plain"

        def detect(self, root):
            p = root / "GLOSSARY.txt"
            return [p] if p.exists() else []

        def read_terms(self, root):
            return [{"term": line.strip(), "domain": None, "definition": "",
                     "source": self.name}
                    for p in self.detect(root)
                    for line in p.read_text().splitlines() if line.strip()]

    (tmp_path / "GLOSSARY.txt").write_text("alpha\nbeta\n")
    ss.register_source(PlainListSource())
    assert sorted(t["term"] for t in ss.load_terms(tmp_path)) == ["alpha", "beta"]


def test_malformed_glossary_does_not_break_serving(tmp_path):
    _write(tmp_path, "docs/_terms/term-map.md", "```yaml term-map\n: : bad yaml :\n```\n")
    assert ss.load_terms(tmp_path) == []            # degrades, never raises
