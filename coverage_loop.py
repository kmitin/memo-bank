"""Demand-driven coverage loop — surface uncovered *changed* code as a ranked
spec-wanted backlog. Reads corpora via the federation (read-only); writes demand
state to a tool-owned backlog store. Non-blocking (always exits 0).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import memo_bank as mb  # noqa: E402
from coverage_backlog import LocalBacklogStore, Miss  # noqa: E402
from project_config import load as load_project_config  # noqa: E402

_IGNORE_SUFFIXES = (".md", ".json", ".lock", ".toml", ".yml", ".yaml", ".txt", ".png", ".svg")


def is_code_path(rel: str, source_globs: list[str]) -> bool:
    """True if a repo-relative path is application source per the project's
    source_globs (config-driven; no hardcoded convention)."""
    if rel.endswith(_IGNORE_SUFFIXES):
        return False
    return any(mb._glob_matches(g, rel) for g in source_globs)


def changed_files(root: Path, base: str | None, staged: bool) -> list[str]:
    """Repo-relative changed paths in `root` (its own git repo)."""
    if staged:
        cmd = ["git", "-C", str(root), "diff", "--name-only", "--cached"]
    else:
        ref = base or "main"
        cmd = ["git", "-C", str(root), "diff", "--name-only", f"{ref}...HEAD"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        return []
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def _is_covered(fed, rel_path: str) -> bool:
    return bool(mb.fed_resolve_path(fed, rel_path)["results"])


def collect_misses(fed, refs, base, staged, source_globs) -> list[Miss]:
    """Uncovered, code-relevant changed files across all slices."""
    misses: list[Miss] = []
    for ref in refs:
        for rel in changed_files(ref.root, base, staged):
            if not is_code_path(rel, source_globs):
                continue
            if not _is_covered(fed, rel):
                misses.append(Miss(path=rel, slice=ref.name))
    return misses


def now_covered(fed, entries) -> list[str]:
    """Backlog paths that now resolve to a governing spec (demand satisfied)."""
    return [e.path for e in entries if _is_covered(fed, e.path)]


def run(registry_path: Path, base: str | None, staged: bool, store_path: Path) -> dict:
    cfg = load_project_config(registry_path)
    fed = mb.load_federation(cfg.slices)
    store = LocalBacklogStore(store_path)
    misses = collect_misses(fed, cfg.slices, base, staged, cfg.source_globs)
    removals = now_covered(fed, store.load())
    res = store.reconcile(misses, removals)
    return {
        "uncovered": sorted(m.path for m in misses),
        "added": res.added,
        "incremented": res.incremented,
        "removed": res.removed,
        "backlog_size": len(store.load()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # Defaults are PROJECT-local (cwd), never engine-local: the engine is installed
    # outside the project, so its own directory must never collect a project's state.
    ap.add_argument("--registry", type=Path, default=Path(".island-slices.json"))
    ap.add_argument("--store", type=Path, default=None,
                    help="backlog store (default: .memobank-backlog.json beside the registry)")
    ap.add_argument("--mode", choices=["ci", "staged"], default="ci")
    ap.add_argument("--base", default="main", help="merge-base ref for --mode ci")
    ap.add_argument("--render", action="store_true", help="print the markdown backlog and exit")
    args = ap.parse_args()

    store_path = args.store or (args.registry.resolve().parent / ".memobank-backlog.json")
    store = LocalBacklogStore(store_path)
    if args.render:
        print(store.render_markdown())
        return 0

    rep = run(args.registry, base=args.base, staged=(args.mode == "staged"),
              store_path=store_path)
    if rep["uncovered"]:
        print(f"⚠ {len(rep['uncovered'])} changed file(s) have no governing spec "
              f"— added to the spec-wanted backlog ({store_path}):", file=sys.stderr)
        for p in rep["uncovered"]:
            print(f"  - {p}", file=sys.stderr)
    else:
        print("✓ all changed code paths are covered by a governing spec.", file=sys.stderr)
    print(f"backlog: +{len(rep['added'])} new, ~{len(rep['incremented'])} seen-again, "
          f"-{len(rep['removed'])} satisfied; {rep['backlog_size']} open.", file=sys.stderr)
    return 0  # non-blocking, always


if __name__ == "__main__":
    raise SystemExit(main())
