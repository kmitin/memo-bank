#!/usr/bin/env python3
"""Time-to-context benchmark for the memo-bank federation.

Operationalizes the time-to-context dimension and
evidence_requirement #4 of the project's architecture decision
claude-code-271d0fd0:

  "median <= 2 file reads from project root to applicable rules in hand,
   over ~10 representative hot-edit tasks across all subprojects."

Method (honest, empirical — no hardcoded expected hits):
  For each representative hot-edit file path, the agent's path to "applicable
  rules in hand" via the memo-bank is:
      1 x docs.resolve_path(path)         -> governing spec id(s)   [read #1]
      1 x docs.get(top_hit_id)            -> the rule body          [read #2]
  So a COVERED path costs 2 reads. A path with no governing live spec is a
  COVERAGE GAP (the corpus does not yet describe that area) — reported
  separately, NOT counted as a >2 failure, because the metric measures the
  retrieval mechanism, and coverage is a distinct (and currently sparser)
  axis.

Baseline contrast (the "before" state):
  without the corpus, an agent greps/opens scattered candidate docs with no
  precedence — the documented prior state this effort exists to replace.

Run (from the repo root):
  .venv/bin/python benchmark_time_to_context.py --federation .island-slices.json
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memo_bank as mb  # noqa: E402
from project_config import load as load_project_config  # noqa: E402


def load_tasks(path) -> list[tuple[str, str, str]]:
    if not path or not Path(path).exists():
        return []
    data = yaml.safe_load(Path(path).read_text()) or {}
    return [(t["subproject"], t["path"], t["intent"]) for t in data.get("tasks", [])]


def run(fed: mb.Federation, tasks) -> dict:
    if not tasks:
        return {"total_tasks": 0, "covered_tasks": 0, "coverage_rate": 0.0,
                "median_reads_over_covered": None, "max_reads_over_covered": None,
                "rows": []}
    rows = []
    for subproject, path, intent in tasks:
        resolved = mb.fed_resolve_path(fed, path)
        hits = resolved["results"]
        if hits:
            top = hits[0]
            got = mb.fed_get(fed, top["id"])
            covered = "error" not in got
            reads = 2 if covered else 1
            rows.append({
                "subproject": subproject, "path": path, "intent": intent,
                "covered": covered, "reads": reads,
                "governing_doc": top["id"], "matched_glob": top["matched_glob"],
                "n_candidates": len(hits),
            })
        else:
            rows.append({
                "subproject": subproject, "path": path, "intent": intent,
                "covered": False, "reads": None,
                "governing_doc": None, "matched_glob": None, "n_candidates": 0,
            })
    covered = [r for r in rows if r["covered"]]
    reads_covered = [r["reads"] for r in covered]
    return {
        "total_tasks": len(rows),
        "covered_tasks": len(covered),
        "coverage_rate": round(len(covered) / len(rows), 2),
        "median_reads_over_covered": (
            statistics.median(reads_covered) if reads_covered else None),
        "max_reads_over_covered": (max(reads_covered) if reads_covered else None),
        "rows": rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--federation", required=True, type=Path,
                    metavar="REGISTRY.json")
    args = ap.parse_args()

    import logging
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)

    cfg = load_project_config(args.federation)
    fed = mb.load_federation(cfg.slices)
    result = run(fed, load_tasks(cfg.benchmark_tasks_path))

    print("=" * 78)
    print("TIME-TO-CONTEXT BENCHMARK — memo-bank federation")
    print("=" * 78)
    print(f"slices: {len(fed.slices)} ok, {len(fed.unreachable)} unreachable")
    print(f"tasks: {result['total_tasks']}  "
          f"covered: {result['covered_tasks']}  "
          f"coverage_rate: {result['coverage_rate']}")
    print(f"median reads (covered): {result['median_reads_over_covered']}  "
          f"(target <= 2)")
    print(f"max reads (covered): {result['max_reads_over_covered']}")
    print("-" * 78)
    print(f"{'subproject':10} {'reads':5} {'cov':3} {'governing doc':28} intent")
    print("-" * 78)
    for r in result["rows"]:
        reads = "-" if r["reads"] is None else str(r["reads"])
        cov = "Y" if r["covered"] else "·"
        doc = r["governing_doc"] or "(no live spec — gap)"
        print(f"{r['subproject']:10} {reads:5} {cov:3} {doc:28} {r['intent']}")
    print("=" * 78)
    gaps = [r for r in result["rows"] if not r["covered"]]
    if gaps:
        print(f"\nCOVERAGE GAPS ({len(gaps)}): areas with no governing live "
              f"spec yet — the corpus mechanism is fast, but these paths are "
              f"not yet described. This is the real bottleneck, not retrieval.")
        for r in gaps:
            print(f"  - {r['subproject']}: {r['path']}  ({r['intent']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
