import json
import subprocess
from pathlib import Path

import coverage_loop as cl
import memo_bank as mb


def test_is_code_path_matches_source_globs():
    globs = ["src/**"]
    assert cl.is_code_path("src/services/api.ts", globs) is True
    assert cl.is_code_path("src/main/java/example/server/X.java", globs) is True
    assert cl.is_code_path("docs/specs/x.md", globs) is False
    assert cl.is_code_path("package.json", globs) is False
    assert cl.is_code_path("Makefile", globs) is False
    assert cl.is_code_path("tools/memo-bank/coverage_loop.py", globs) is False


def test_is_code_path_honors_custom_globs():
    assert cl.is_code_path("app/page.tsx", ["app/**"]) is True
    assert cl.is_code_path("src/x.ts", ["app/**"]) is False


def test_is_code_path_excludes_suffix_under_glob():
    # a path can match the source glob yet be excluded by the suffix-ignore list
    assert cl.is_code_path("src/schema.json", ["src/**"]) is False
    assert cl.is_code_path("src/notes.md", ["src/**"]) is False


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, text=True)


def test_changed_files_staged(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("x")
    _git(tmp_path, "add", "src/a.ts")
    files = cl.changed_files(tmp_path, base=None, staged=True)
    assert files == ["src/a.ts"]


def _make_slice(tmp_path, name, spec_id, applies_to):
    specs = tmp_path / name / "docs" / "specs"
    specs.mkdir(parents=True)
    fm = (
        "---\n"
        f"id: {spec_id}\nkind: spec\nsubproject: {name}\n"
        "title: t\nowner: o\nstatus: active\nlast_reviewed: 2026-06-17\n"
        "applies_to:\n" + "".join(f"  - {g}\n" for g in applies_to) +
        "tags:\n  - t\nrelated: []\nindexed: true\n---\n\n"
        "# t\n\n## Problem\nx\n\n## Contract\n1. x\n\n"
        "## Restrictions (admissibility)\n- NEVER x\n\n## Open threads\nx\n\n## Code references\nx\n"
    )
    (specs / f"{spec_id}.md").write_text(fm)
    return mb.SliceRef(name=name, root=tmp_path / name)


def test_now_covered_detects_resolution(tmp_path):
    ref = _make_slice(tmp_path, "frontend", "covered-spec", ["src/covered/**"])
    fed = mb.load_federation([ref])
    from coverage_backlog import BacklogEntry
    entries = [
        BacklogEntry("src/covered/x.ts", "frontend", "2026-06-17", "2026-06-17", 1),
        BacklogEntry("src/uncovered/y.ts", "frontend", "2026-06-17", "2026-06-17", 1),
    ]
    assert cl.now_covered(fed, entries) == ["src/covered/x.ts"]


def test_run_reconciles_and_is_nonblocking(tmp_path):
    ref = _make_slice(tmp_path, "frontend", "covered-spec", ["src/covered/**"])
    root = ref.root
    _git(root, "init", "-q"); _git(root, "config", "user.email", "t@t"); _git(root, "config", "user.name", "t")
    (root / "src" / "covered").mkdir(parents=True); (root / "src" / "uncovered").mkdir(parents=True)
    (root / "src" / "covered" / "x.ts").write_text("x")
    (root / "src" / "uncovered" / "y.ts").write_text("y")
    (root / "Makefile").write_text("x")
    _git(root, "add", "src/covered/x.ts", "src/uncovered/y.ts", "Makefile")
    registry = tmp_path / ".island-slices.json"
    registry.write_text(json.dumps({"island": "t", "version": 1,
                                    "slices": [{"name": "frontend", "root": "frontend"}]}))
    store_path = tmp_path / "backlog.json"
    report = cl.run(registry, base=None, staged=True, store_path=store_path)
    assert report["uncovered"] == ["src/uncovered/y.ts"]
    assert report["added"] == ["src/uncovered/y.ts"]
    entries = json.loads(store_path.read_text())["entries"]
    assert [e["path"] for e in entries] == ["src/uncovered/y.ts"]
    # default source_globs (registry has none): src/ paths classified, Makefile (non-src) filtered out
    assert "Makefile" not in report["uncovered"]
    assert report["uncovered"] == ["src/uncovered/y.ts"]
