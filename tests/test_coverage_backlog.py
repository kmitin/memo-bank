from pathlib import Path
from coverage_backlog import LocalBacklogStore, Miss


def test_reconcile_adds_new_miss(tmp_path):
    store = LocalBacklogStore(tmp_path / "backlog.json")
    res = store.reconcile([Miss("src/a.ts", "frontend")], [], today="2026-06-17")
    assert res.added == ["src/a.ts"]
    entries = {e.path: e for e in store.load()}
    assert entries["src/a.ts"].hits == 1
    assert entries["src/a.ts"].slice == "frontend"
    assert entries["src/a.ts"].first_seen == "2026-06-17"


def test_reconcile_increments_recurring(tmp_path):
    store = LocalBacklogStore(tmp_path / "backlog.json")
    store.reconcile([Miss("src/a.ts", "frontend")], [], today="2026-06-17")
    res = store.reconcile([Miss("src/a.ts", "frontend")], [], today="2026-06-18")
    assert res.incremented == ["src/a.ts"]
    e = {e.path: e for e in store.load()}["src/a.ts"]
    assert e.hits == 2
    assert e.first_seen == "2026-06-17" and e.last_seen == "2026-06-18"


def test_reconcile_removes_covered(tmp_path):
    store = LocalBacklogStore(tmp_path / "backlog.json")
    store.reconcile([Miss("src/a.ts", "frontend")], [], today="2026-06-17")
    res = store.reconcile([], ["src/a.ts"], today="2026-06-18")
    assert res.removed == ["src/a.ts"]
    assert store.load() == []


def test_render_markdown_sorts_by_hits_desc(tmp_path):
    store = LocalBacklogStore(tmp_path / "backlog.json")
    store.reconcile([Miss("src/a.ts", "frontend"), Miss("src/b.ts", "server")], [], today="2026-06-17")
    store.reconcile([Miss("src/b.ts", "server")], [], today="2026-06-18")
    md = store.render_markdown()
    assert md.index("src/b.ts") < md.index("src/a.ts")  # b has more hits
    assert "| hits |" in md
