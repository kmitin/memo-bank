"""Project config for the memo-bank engine — the single adoption contract.

Reads the extended .island-slices.json (slices + source_globs + eval refs +
schema) into a typed ProjectConfig so engine code carries no project literals.
All extended fields are optional with defaults: a pre-existing registry loads
and behaves exactly as before.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import memo_bank as mb  # noqa: E402


@dataclass
class ProjectConfig:
    registry_path: Path
    island: str
    slices: list  # list[mb.SliceRef]
    source_globs: list[str]
    benchmark_tasks_path: Path | None
    # gold_queries_path and schema_path are part of the adoption contract (a new
    # project declares them) but are not yet consumed by runtime engine code —
    # run_eval/build_corpus gold rewiring is deferred. Exposed, not yet wired.
    gold_queries_path: Path | None
    schema_path: Path | None


def load(registry_path: Path) -> ProjectConfig:
    registry_path = Path(registry_path)
    data = json.loads(registry_path.read_text())
    base = registry_path.resolve().parent

    def _resolve(rel: str | None) -> Path | None:
        return (base / rel).resolve() if rel else None

    ev = data.get("eval") or {}
    return ProjectConfig(
        registry_path=registry_path,
        island=data.get("island", "island"),
        slices=mb.parse_registry(registry_path),
        source_globs=list(data.get("source_globs") or ["src/**"]),
        benchmark_tasks_path=_resolve(ev.get("benchmark_tasks")),
        gold_queries_path=_resolve(ev.get("gold_queries")),
        schema_path=_resolve(data.get("schema")),
    )
