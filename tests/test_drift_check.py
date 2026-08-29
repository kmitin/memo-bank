import subprocess
from pathlib import Path

import drift_check as dc


def test_governed_changes_filters_by_glob_and_excludes_self():
    changed = {"src/widget/x.ts", "src/other/y.ts", "docs/specs/widget.md"}
    out = dc.governed_changes(["src/widget/**"], changed, self_rel="docs/specs/widget.md")
    assert out == ["src/widget/x.ts"]


def test_governed_changes_excludes_the_doc_itself():
    changed = {"docs/specs/widget.md", "src/widget/x.ts"}
    # even if a doc glob would match itself, the doc's own path is never "drift"
    out = dc.governed_changes(["docs/**", "src/widget/**"], changed, self_rel="docs/specs/widget.md")
    assert "docs/specs/widget.md" not in out
    assert out == ["src/widget/x.ts"]


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def _spec(root, doc_id, applies_to, last_reviewed):
    specs = root / "docs" / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    (specs / f"{doc_id}.md").write_text(
        f"---\nid: {doc_id}\nkind: spec\nsubproject: umbrella\ntitle: {doc_id}\n"
        f"owner: t\nstatus: active\nlast_reviewed: {last_reviewed}\n"
        "applies_to:\n" + "".join(f"  - {g}\n" for g in applies_to) +
        "tags:\n  - t\nrelated: []\nindexed: true\n---\n# t\nbody\n")


def test_find_drift_flags_governed_file_changed_after_last_reviewed(tmp_path):
    import memo_bank as mb
    root = tmp_path
    _git(root, "init", "-q"); _git(root, "config", "user.email", "t@t"); _git(root, "config", "user.name", "t")
    # a stale spec (reviewed in 2020) governing src/widget/**
    _spec(root, "stale-spec", ["src/widget/**"], "2020-01-01")
    # a fresh spec (reviewed today) governing src/other/** with no changes since
    _spec(root, "fresh-spec", ["src/other/**"], "2999-01-01")
    (root / "src" / "widget").mkdir(parents=True)
    (root / "src" / "widget" / "x.ts").write_text("changed code\n")
    _git(root, "add", "."); _git(root, "commit", "-q", "-m", "code + specs")  # commit date = now (> 2020)
    (root / ".island-slices.json").write_text(
        '{"island":"t","version":1,"slices":[{"name":"main","root":"."}]}')

    fed = mb.load_federation(mb.parse_registry(root / ".island-slices.json"))
    findings = {f.doc_id: f for f in dc.find_drift(fed)}
    assert "stale-spec" in findings                      # governed file changed after 2020 review
    assert "src/widget/x.ts" in findings["stale-spec"].changed
    assert findings["stale-spec"].last_reviewed == "2020-01-01"
    assert "fresh-spec" not in findings                  # reviewed in the future / nothing changed since


def test_find_drift_ignores_doc_and_governance_changes(tmp_path):
    """A meta-spec governing docs/** must NOT flag when only docs change —
    a doc changing is not code drift."""
    import memo_bank as mb
    root = tmp_path
    _git(root, "init", "-q"); _git(root, "config", "user.email", "t@t"); _git(root, "config", "user.name", "t")
    _spec(root, "schema-ref", ["docs/**"], "2020-01-01")          # governs the corpus itself
    (root / ".island-slices.json").write_text(
        '{"island":"t","version":1,"slices":[{"name":"main","root":"."}]}')
    _git(root, "add", "."); _git(root, "commit", "-q", "-m", "docs only")
    fed = mb.load_federation(mb.parse_registry(root / ".island-slices.json"))
    assert "schema-ref" not in {f.doc_id for f in dc.find_drift(fed)}


def test_same_day_review_covers_same_day_commits(tmp_path):
    """A doc reviewed today is not 'stale' against code committed today —
    last_reviewed is date-granular and covers the whole review day."""
    import datetime
    import memo_bank as mb
    root = tmp_path
    _git(root, "init", "-q"); _git(root, "config", "user.email", "t@t"); _git(root, "config", "user.name", "t")
    today = datetime.date.today().isoformat()
    _spec(root, "today-spec", ["src/today/**"], today)
    (root / "src" / "today").mkdir(parents=True)
    (root / "src" / "today" / "z.ts").write_text("code committed today\n")
    _git(root, "add", "."); _git(root, "commit", "-q", "-m", "same-day")
    (root / ".island-slices.json").write_text(
        '{"island":"t","version":1,"slices":[{"name":"main","root":"."}]}')
    fed = mb.load_federation(mb.parse_registry(root / ".island-slices.json"))
    assert "today-spec" not in {f.doc_id for f in dc.find_drift(fed)}


def test_run_is_nonblocking(tmp_path):
    _git(tmp_path, "init", "-q"); _git(tmp_path, "config", "user.email", "t@t"); _git(tmp_path, "config", "user.name", "t")
    _spec(tmp_path, "s", ["src/**"], "2999-01-01")
    (tmp_path / ".island-slices.json").write_text(
        '{"island":"t","version":1,"slices":[{"name":"main","root":"."}]}')
    rc = dc.run(tmp_path / ".island-slices.json")
    assert rc == 0


def test_drift_detected_over_a_foreign_ecosystem_artifact(tmp_path):
    """The payoff of adapters: an ADR from another methodology has no
    `applies_to`/`last_reviewed`, yet drift is still checkable — it governs the
    files it mentions, and its own commit date stands in for the review date."""
    import memo_bank as mb

    root = tmp_path
    _git(root, "init", "-q"); _git(root, "config", "user.email", "t@t"); _git(root, "config", "user.name", "t")
    (root / ".island-slices.json").write_text(
        '{"island":"t","version":1,"slices":[{"name":"main","root":"."}]}')
    _spec(root, "unrelated", ["docs/**"], "2999-01-01")     # corpus doc that must NOT flag

    # a grill-with-docs ADR that names the file it governs, committed in the past
    adr = root / "docs" / "adr"; adr.mkdir(parents=True)
    (adr / "0001-db.md").write_text("We chose postgres. Governs `src/db/pool.py`.\n")
    (root / "src").mkdir()
    (root / "src" / "pool.py").write_text("old\n")
    _git(root, "add", "."); _git(root, "commit", "-q", "-m", "adr + code",
         "--date", "2020-01-01T00:00:00")

    # the governed file changes AFTER the ADR was last touched
    (root / "src" / "db").mkdir(parents=True)
    (root / "src" / "db" / "pool.py").write_text("new implementation\n")
    _git(root, "add", "."); _git(root, "commit", "-q", "-m", "rewrite pool")

    findings = dc.find_adapter_drift(root, "main")
    ids = {f.doc_id for f in findings}
    assert "grill-with-docs:0001-db.md" in ids, findings
    flagged = next(f for f in findings if f.doc_id == "grill-with-docs:0001-db.md")
    assert "src/db/pool.py" in flagged.changed
