"""Doc-drift check — the stale-spec sibling of the coverage loop.

The coverage loop catches *missing* specs (source with no governing doc). This
catches *stale* ones: a governed file that changed (committed since, or currently
modified/staged) AFTER its governing doc's `last_reviewed` date. Read-only on the
corpora; non-blocking (always exits 0). Stateless — recomputed from git +
frontmatter each run, so a finding clears the moment the doc is refreshed.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import frontmatter  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import memo_bank as mb  # noqa: E402
from project_config import load as load_project_config  # noqa: E402
import spec_sources  # noqa: E402


# The documentation/governance layers are not "governed code" — a corpus doc or
# haft-graph file changing is not drift (that's the validator's / the doc's own
# concern). Excluding them keeps schema-reference meta-specs from false-flagging.
_META_PREFIXES = ("docs/", ".haft/")


@dataclass
class DriftFinding:
    doc_id: str
    slice: str
    last_reviewed: str
    changed: list[str]


def governed_changes(applies_to: list[str], changed: set[str], self_rel: str) -> list[str]:
    """Changed files that match the doc's applies_to globs — excluding the doc itself."""
    return sorted(
        f for f in changed
        if f != self_rel and any(mb._glob_matches(g, f) for g in applies_to))


def _changed_since(root: Path, since: str) -> set[str]:
    """Repo-relative paths in `root` that changed since `since` (a YYYY-MM-DD date):
    committed after that date, plus anything currently modified or staged."""
    out: set[str] = set()
    # end-of-day: a review dated D covers all of day D, so same-day commits are not
    # drift (last_reviewed is date-granular; avoids a same-day false positive).
    cmds = [
        ["git", "-C", str(root), "log", f"--since={since} 23:59:59", "--name-only", "--pretty=format:"],
        ["git", "-C", str(root), "diff", "--name-only"],
        ["git", "-C", str(root), "diff", "--name-only", "--cached"],
    ]
    for cmd in cmds:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode == 0:
            out |= {ln.strip() for ln in r.stdout.splitlines() if ln.strip()}
    return out


def _artifact_date(root: Path, path: Path) -> str | None:
    """When the artifact was last touched — its last commit date, or mtime.

    Foreign ecosystems don't carry `last_reviewed`, so the artifact's own history
    stands in for it. Author date (%as), not committer date: a rebase or amend
    shouldn't read as "the document was reviewed today"."""
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = str(path)
    r = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%as", "--", rel],
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    try:
        import datetime
        return datetime.date.fromtimestamp(path.stat().st_mtime).isoformat()
    except OSError:
        return None


def find_adapter_drift(root: Path, slice_name: str) -> list[DriftFinding]:
    """Drift over artifacts contributed by spec-source adapters.

    The corpus pass below needs frontmatter (`applies_to` + `last_reviewed`).
    Methodologies like grill-with-docs ADRs or superpowers designs have neither,
    so their adapter reports which files the document *mentions*, and the
    document's own last-commit date stands in for the review date. That is what
    makes drift checkable across any registered ecosystem."""
    findings: list[DriftFinding] = []
    cache: dict[str, set[str]] = {}
    for art in spec_sources.all_artifacts(root):
        if not art.governs:
            continue
        reviewed = _artifact_date(root, art.path)
        if not reviewed:
            continue
        if reviewed not in cache:
            cache[reviewed] = {f for f in _changed_since(root, reviewed)
                               if not f.startswith(_META_PREFIXES)}
        try:
            self_rel = art.path.relative_to(root).as_posix()
        except ValueError:
            self_rel = art.path.name
        changed = governed_changes(list(art.governs), cache[reviewed], self_rel)
        if changed:
            findings.append(DriftFinding(
                doc_id=f"{art.source}:{art.path.name}", slice=slice_name,
                last_reviewed=reviewed, changed=changed))
    return findings


def find_drift(fed) -> list[DriftFinding]:
    """For each hot doc carrying last_reviewed + applies_to, find governed files
    that changed since the review."""
    findings: list[DriftFinding] = []
    cache: dict[tuple[str, str], set[str]] = {}
    seen_roots: set[Path] = set()
    for name, corpus in fed.slices.items():
        for d in corpus.by_id.values():
            if d.kind not in ("spec", "state") or not d.indexed or not d.applies_to:
                continue
            try:
                lr = frontmatter.load(d.corpus_root / d.rel_path).get("last_reviewed")
            except Exception:
                continue
            if not lr:
                continue
            lr = str(lr)
            key = (str(d.corpus_root), lr)
            if key not in cache:
                cache[key] = {f for f in _changed_since(d.corpus_root, lr)
                              if not f.startswith(_META_PREFIXES)}
            changed = governed_changes(d.applies_to, cache[key], d.rel_path)
            if changed:
                findings.append(DriftFinding(doc_id=d.id, slice=name,
                                             last_reviewed=lr, changed=changed))
        # artifacts from other methodologies present in this slice
        root = next((c.corpus_root for c in corpus.by_id.values()), None) or Path(".")
        if root not in seen_roots:
            seen_roots.add(root)
            findings.extend(find_adapter_drift(root, name))
    return sorted(findings, key=lambda f: (-len(f.changed), f.slice, f.doc_id))


def run(registry_path: Path) -> int:
    fed = mb.load_federation(load_project_config(registry_path).slices)
    findings = find_drift(fed)
    if findings:
        print(f"⚠ {len(findings)} governing doc(s) may be stale — governed code changed "
              f"since their last_reviewed; consider a memo-bank refresh:", file=sys.stderr)
        for f in findings:
            shown = ", ".join(f.changed[:5]) + (" …" if len(f.changed) > 5 else "")
            print(f"  - {f.slice}:{f.doc_id} (last_reviewed {f.last_reviewed}) "
                  f"— {len(f.changed)} changed: {shown}", file=sys.stderr)
    else:
        print("✓ no doc drift: every governing doc is newer than its governed code.", file=sys.stderr)
    return 0  # non-blocking, always


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--registry", type=Path, default=Path(".island-slices.json"))  # project-local
    args = ap.parse_args()
    return run(args.registry)


if __name__ == "__main__":
    raise SystemExit(main())
