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
    assert [s.name for s in ss.sources()] == [
        "haft", "docs-native", "grill-with-docs", "superpowers"]


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


# ---------------------------------------------------------------------------
# Artifacts: adapters declare WHERE a methodology keeps governing documents,
# which is what lets the drift check reason about foreign ecosystems.
# ---------------------------------------------------------------------------

def test_mentioned_paths_extracts_referenced_source_files(tmp_path):
    doc = tmp_path / "adr.md"
    doc.write_text(
        "We changed `src/services/api.ts` and src/db/pool.py.\n"
        "See https://example.com/docs/thing.html for background.\n"
        "Also tests/test_api.py.\n")
    got = ss.mentioned_paths(doc)
    assert "src/services/api.ts" in got
    assert "src/db/pool.py" in got
    assert "tests/test_api.py" in got
    assert not any(g.startswith("http") for g in got)      # URLs are not paths


def test_grill_with_docs_adapter_reads_context_and_adrs(tmp_path):
    (tmp_path / "CONTEXT.md").write_text(
        "# Context\n\n- **widget-token** — the opaque handle a widget presents.\n"
        "- **grill** — the interview loop.\n")
    adr = tmp_path / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001-use-postgres.md").write_text("We use postgres. Touches `src/db/pool.py`.\n")

    names = {n for n, _ in ss.detect_all(tmp_path)}
    assert "grill-with-docs" in names

    terms = {t["term"]: t for t in ss.load_terms(tmp_path)}
    assert "widget-token" in terms and terms["widget-token"]["source"] == "grill-with-docs"

    arts = {a.path.name: a for a in ss.all_artifacts(tmp_path)}
    assert arts["CONTEXT.md"].kind == "glossary"
    assert arts["0001-use-postgres.md"].kind == "decision"
    assert "src/db/pool.py" in arts["0001-use-postgres.md"].governs


def test_superpowers_adapter_picks_up_designs_and_plans(tmp_path):
    base = tmp_path / "docs" / "superpowers"
    (base / "specs").mkdir(parents=True)
    (base / "plans").mkdir(parents=True)
    (base / "specs" / "2026-06-17-thing-design.md").write_text("Design. Governs `src/thing.py`.\n")
    (base / "plans" / "2026-06-18-thing.md").write_text("Plan for src/thing.py\n")

    arts = {a.path.name: a for a in ss.all_artifacts(tmp_path)}
    assert arts["2026-06-17-thing-design.md"].kind == "design"
    assert arts["2026-06-18-thing.md"].kind == "plan"
    assert all(a.source == "superpowers" for a in arts.values())
    assert "src/thing.py" in arts["2026-06-17-thing-design.md"].governs


def test_artifacts_are_collected_across_every_source(tmp_path):
    _write(tmp_path, ".haft/specs/term-map.md")
    (tmp_path / "CONTEXT.md").write_text("- **a** — b\n")
    sources = {a.source for a in ss.all_artifacts(tmp_path)}
    assert sources == {"haft", "grill-with-docs"}


def test_absent_ecosystems_contribute_nothing(tmp_path):
    assert ss.all_artifacts(tmp_path) == []
