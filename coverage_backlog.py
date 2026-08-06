"""Tool-owned 'spec-wanted' backlog for the demand-driven coverage loop.

The memo-bank is the SOLE writer of this state (GitOps/ArgoCD-shaped): it
reconciles currently-uncovered changed paths into a persisted backlog. Source
corpora are never written. The store target is abstracted so this local file can
later be swapped for a dedicated git repo (GitRepoBacklogStore) with no loop rework.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path


@dataclass
class Miss:
    path: str
    slice: str


@dataclass
class BacklogEntry:
    path: str
    slice: str
    first_seen: str
    last_seen: str
    hits: int


@dataclass
class ReconcileResult:
    added: list[str]
    incremented: list[str]
    removed: list[str]


class LocalBacklogStore:
    """JSON-backed backlog under a tool-owned path. Sole-writer reconcile."""

    def __init__(self, store_path: Path):
        self.store_path = Path(store_path)

    def load(self) -> list[BacklogEntry]:
        if not self.store_path.exists():
            return []
        data = json.loads(self.store_path.read_text())
        return [BacklogEntry(**e) for e in data.get("entries", [])]

    def _save(self, entries: list[BacklogEntry]) -> None:
        entries = sorted(entries, key=lambda e: (-e.hits, e.slice, e.path))
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        self.store_path.write_text(
            json.dumps({"entries": [asdict(e) for e in entries]}, indent=2) + "\n")

    def reconcile(self, upserts: list[Miss], removals: list[str],
                  today: str | None = None) -> ReconcileResult:
        today = today or date.today().isoformat()
        entries = {e.path: e for e in self.load()}
        added: list[str] = []
        incremented: list[str] = []
        removed: list[str] = []

        for m in sorted(upserts, key=lambda m: m.path):
            if m.path in entries:
                e = entries[m.path]
                e.hits += 1
                e.last_seen = today
                incremented.append(m.path)
            else:
                entries[m.path] = BacklogEntry(
                    path=m.path, slice=m.slice,
                    first_seen=today, last_seen=today, hits=1)
                added.append(m.path)

        for path in removals:
            if path in entries:
                del entries[path]
                removed.append(path)

        self._save(list(entries.values()))
        return ReconcileResult(added=added, incremented=incremented, removed=removed)

    def render_markdown(self) -> str:
        rows = sorted(self.load(), key=lambda e: (-e.hits, e.slice, e.path))
        lines = [
            "# Spec-wanted backlog (demand-driven coverage loop)",
            "",
            "Tool-managed — the memo-bank coverage loop is the sole writer.",
            "",
            "| hits | slice | path | first_seen | last_seen |",
            "|---|---|---|---|---|",
        ]
        for e in rows:
            lines.append(f"| {e.hits} | {e.slice} | {e.path} | {e.first_seen} | {e.last_seen} |")
        return "\n".join(lines) + "\n"
