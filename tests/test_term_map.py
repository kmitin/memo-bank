"""Term-map source resolution — resolve_term must work WITHOUT haft.

The 8th tool originally read `.haft/specs/term-map.md`, coupling the engine to a
separate private tool. A haft-free adopter must still get terms (from a
docs-native location) and must degrade cleanly when there is no term map at all.
"""
from pathlib import Path

import memo_bank as mb

TERM_MAP = """```yaml term-map
status: draft
entries:
  - term: widget-token
    domain: target-system
    definition: |
      The opaque handle a widget presents to the API.
```
"""


def _corpus_root(tmp_path: Path, term_map_rel: str | None) -> Path:
    (tmp_path / "docs" / "specs").mkdir(parents=True)
    if term_map_rel:
        p = tmp_path / term_map_rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(TERM_MAP)
    return tmp_path


def test_terms_load_from_docs_native_location_without_haft(tmp_path):
    root = _corpus_root(tmp_path, "docs/_terms/term-map.md")
    assert not (root / ".haft").exists()
    terms = mb._load_terms(root)
    assert [t["term"] for t in terms] == ["widget-token"]
    assert terms[0]["domain"] == "target-system"


def test_terms_still_load_from_haft_when_present(tmp_path):
    root = _corpus_root(tmp_path, ".haft/specs/term-map.md")
    assert [t["term"] for t in mb._load_terms(root)] == ["widget-token"]


def test_no_term_map_yields_no_terms_and_absent_verdict(tmp_path):
    root = _corpus_root(tmp_path, None)
    assert mb._load_terms(root) == []
    (root / ".island-slices.json").write_text(
        '{"island":"t","version":1,"slices":[{"name":"main","root":"."}]}')
    fed = mb.load_federation(mb.parse_registry(root / ".island-slices.json"))
    r = mb.fed_resolve_term(fed, term="anything")
    assert r["verdict"] == "absent"          # graceful, not an error
    assert r["definitions"] == []


def test_corpus_signature_tracks_the_docs_native_term_map(tmp_path):
    """Reload must notice a term-map edit at either supported location."""
    root = _corpus_root(tmp_path, "docs/_terms/term-map.md")
    before = mb.corpus_signature(root)
    (root / "docs" / "_terms" / "term-map.md").write_text(
        TERM_MAP.replace("widget-token", "widget-token-v2"))
    assert mb.corpus_signature(root) != before
