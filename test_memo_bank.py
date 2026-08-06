"""Engine-generic memo-bank tests — exercise the tool surface against a
SYNTHETIC corpus built in a tmp dir, so they pass in ANY project. These are the
shippable engine tests: when adopting the memo-bank elsewhere, copy this file
(it has no project coupling). Project-specific corpus assertions live in a
separate `test_corpus_<project>.py` that is NOT part of the engine.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memo_bank as mb  # noqa: E402


def _write(specs_dir: Path, doc_id: str, *, kind: str, applies_to: list[str],
           tags: list[str], body: str, indexed: bool = True) -> None:
    specs_dir.mkdir(parents=True, exist_ok=True)
    at = "[]" if not applies_to else "\n" + "".join(f"  - {g}\n" for g in applies_to).rstrip("\n")
    tg = "".join(f"  - {t}\n" for t in tags).rstrip("\n")
    (specs_dir / f"{doc_id}.md").write_text(
        f"---\nid: {doc_id}\nkind: {kind}\nsubproject: umbrella\n"
        f"title: {doc_id.replace('-', ' ').title()}\nowner: t\nstatus: active\n"
        f"last_reviewed: 2026-06-18\napplies_to: {at}\ntags:\n{tg}\n"
        f"related: []\nindexed: {str(indexed).lower()}\n---\n{body}\n")


def _build_island(root: Path) -> Path:
    """A 3-doc synthetic corpus: a narrow spec (with sections), a broad spec,
    and an archive entry. Returns the registry path."""
    specs = root / "docs" / "specs"
    archive = root / "docs" / "archive"
    _write(specs, "widget-rules", kind="spec", applies_to=["src/widget/**"],
           tags=["widget", "ui"],
           body="# Widget rules\n\n## Rule\nWidgets must foo before bar.\n\n"
                "## Invariants\nNever bar without foo.\n")
    _write(specs, "broad-src", kind="spec", applies_to=["src/**"],
           tags=["broad"], body="# Broad\n\n## Rule\nAll source is broad.\n")
    _write(archive, "old-widget", kind="archive", applies_to=[],
           tags=["widget", "history"], indexed=False,
           body="# Old widget\nWe used to baz; widget regression legacy.\n")
    reg = root / ".island-slices.json"
    reg.write_text('{"island":"t","version":1,"slices":[{"name":"main","root":"."}]}')
    return reg


@pytest.fixture(scope="module")
def island(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("island")
    _build_island(root)
    return root


def _corpus(island: Path) -> mb.Corpus:
    return mb.load_corpus([("main", island)])


def _fed(island: Path) -> mb.Federation:
    return mb.load_federation(mb.parse_registry(island / ".island-slices.json"))


# ---------- list ----------

def test_list_excludes_archive_by_default(island):
    kinds = {e["kind"] for e in mb.tool_list(_corpus(island))}
    assert "archive" not in kinds and "spec" in kinds


def test_list_includes_archive_when_asked(island):
    ids = [e["id"] for e in mb.tool_list(_corpus(island), include_archive=True)
           if e["kind"] == "archive"]
    assert "old-widget" in ids


def test_list_returns_summaries_not_bodies(island):
    result = mb.tool_list(_corpus(island))
    assert all("body" not in e for e in result)
    for e in result:
        for required in ("id", "kind", "subproject", "title", "corpus", "path", "tags", "indexed"):
            assert required in e, f"missing {required} in {e}"


# ---------- get ----------

def test_get_returns_full_doc(island):
    r = mb.tool_get(_corpus(island), id="widget-rules")
    assert r["id"] == "widget-rules" and "body" in r and "frontmatter" in r


def test_get_returns_error_for_unknown_id(island):
    r = mb.tool_get(_corpus(island), id="does-not-exist")
    assert "error" in r and "not found" in r["error"]


# ---------- resolve_path ----------

def test_resolve_path_orders_by_specificity(island):
    ids = [e["id"] for e in mb.tool_resolve_path(_corpus(island), path="src/widget/x.ts")]
    assert ids[:2] == ["widget-rules", "broad-src"], ids


def test_resolve_path_excludes_archive(island):
    kinds = {e["kind"] for e in mb.tool_resolve_path(_corpus(island), path="src/widget/x.ts")}
    assert "archive" not in kinds


def test_glob_matches_double_star():
    assert mb._glob_matches("src/**", "src/a/b/c.ts")
    assert mb._glob_matches("src/**/auth.ts", "src/services/auth.ts")
    assert mb._glob_matches("**/*.tsx", "deeply/nested/file.tsx")
    assert not mb._glob_matches("src/**", "lib/a.ts")


# ---------- get_section (incremental rung 1) ----------

def test_get_section_returns_one_section_not_whole_doc(island):
    corpus = _corpus(island)
    r = mb.tool_get_section(corpus, id="widget-rules", section="Invariants")
    assert r["found"] is True and r["tokens"] > 0
    full = mb.tool_get(corpus, id="widget-rules")["body"]
    assert r["body"] in full and len(r["body"]) < len(full)


def test_get_section_miss_lists_available_sections(island):
    r = mb.tool_get_section(_corpus(island), id="widget-rules", section="No Such")
    assert r["found"] is False and "Invariants" in r["available_sections"]


# ---------- search (rung 3) ----------

def test_search_live_excludes_archive_returns_pointers(island):
    hits = mb.fed_search_live(_fed(island), query="widget rule foo")["results"]
    assert hits and all(h["kind"] != "archive" for h in hits)
    assert all("body" not in h and "score" in h and "snippet" in h for h in hits)
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)


def test_search_archive_is_archive_only_and_distinct(island):
    fed = _fed(island)
    arch = mb.fed_search_archive(fed, query="widget regression legacy")["results"]
    assert arch and all(h["kind"] == "archive" for h in arch)
    assert "old-widget" in {h["id"] for h in arch}
    live = mb.fed_search_live(fed, query="widget regression legacy")["results"]
    assert all(h["kind"] != "archive" for h in live)


# ---------- compose_context (rung 4) ----------

def test_compose_context_assembles_under_budget(island):
    r = mb.fed_compose_context(_fed(island), path="src/widget/x.ts", budget_tokens=1200)
    assert r["mode"] == "assembled" and r["used_tokens"] <= r["budget_tokens"]
    assert "widget-rules" in [s["id"] for s in r["sections"]]
    assert all(s["body"] and s["tokens"] > 0 for s in r["sections"])


def test_compose_context_degrades_to_id_list_never_truncates(island):
    r = mb.fed_compose_context(_fed(island), path="src/widget/x.ts", budget_tokens=1)
    assert r["mode"] == "id_list" and "widget-rules" in r["candidates"]
    assert "sections" not in r


def test_compose_context_no_candidates_returns_empty_id_list(island):
    r = mb.tool_compose_context(_corpus(island), path="no/such/path.xyz")
    assert r["mode"] == "id_list" and r["candidates"] == []


# ---------- federation mechanics ----------

def test_fed_partial_status_when_slice_unreachable(island, tmp_path):
    refs = [mb.SliceRef(name="main", root=island),
            mb.SliceRef(name="ghost", root=tmp_path / "nope")]
    fed = mb.load_federation(refs)
    assert "ghost" in fed.unreachable
    out = mb.fed_list(fed)
    assert out["partial"] is True
    statuses = {s["slice"]: s["status"] for s in out["slice_status"]}
    assert statuses["ghost"] == "unreachable" and statuses["main"] == "ok"


def test_fed_get_missing_id_reports_partial(tmp_path):
    fed = mb.load_federation([mb.SliceRef(name="ghost", root=tmp_path / "nope")])
    out = mb.fed_get(fed, id="anything")
    assert "error" in out and out["partial"] is True


def test_fed_list_dedupes_and_sorts(island):
    out = mb.fed_list(_fed(island))
    ids = [e["id"] for e in out["results"]]
    assert len(ids) == len(set(ids))
    keys = [(e["kind"], e["subproject"], e["id"]) for e in out["results"]]
    assert keys == sorted(keys)


def test_duplicate_id_strict_raises(island):
    with pytest.raises(mb.DuplicateIdError):
        mb.load_corpus([("a", island), ("b", island)])


def test_duplicate_id_nonstrict_keeps_first(island):
    c = mb.load_corpus([("a", island), ("b", island)], strict_ids=False)
    assert "widget-rules" in c.by_id


def test_reload_picks_up_new_doc(tmp_path):
    reg = _build_island(tmp_path)
    fed = mb.load_federation(mb.parse_registry(reg))
    assert mb.fed_get(fed, id="late-doc").get("error")
    _write(tmp_path / "docs" / "specs", "late-doc", kind="spec", applies_to=[],
           tags=["late"], body="# Late\nadded after load\n")
    reloaded = fed.reload_if_stale()
    assert "main" in reloaded and "late-doc" in fed.slices["main"].by_id


# ---------- tool surface / wiring ----------

def test_all_eight_tools_registered():
    expected = {"docs.list", "docs.get", "docs.get_section", "docs.search_live",
                "docs.search_archive", "docs.resolve_path", "docs.resolve_term",
                "docs.compose_context"}
    assert {t.name for t in mb.TOOLS} == expected


def test_all_eight_tools_live_slice_and_federation():
    expected = {t.name for t in mb.TOOLS}
    assert len(expected) == 8
    assert set(mb.TOOL_DISPATCH) == expected
    assert set(mb.FED_DISPATCH) == expected


def test_build_server_smoke(island):
    assert mb.build_server(_corpus(island)) is not None
    assert mb.build_server(_fed(island), mb.FED_DISPATCH) is not None


def test_split_sections_and_token_estimate():
    secs = dict(mb._split_sections("# T\npre\n\n## Rule\nrule body\n\n## History\nhist\n"))
    assert "Rule" in secs and "rule body" in secs["Rule"]
    assert mb._estimate_tokens("abcd" * 10) == 10
