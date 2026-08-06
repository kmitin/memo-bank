"""Frontmatter validator for a memo-bank docs corpus.

Implements docs/specs/schema-frontmatter-v1.md against docs/**/*.md.
Haft's own carriers (.haft/specs/*.md) use a different fenced format and
are validated by `haft spec check`; this validator delegates them by
default but reads their frontmatter so cross-references can resolve.

Failure mode is always exit-non-zero. No --warn-only flag. Per the V2
decision's invariant: the validator's failure mode is always "fail the
build", never "warn".
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import frontmatter
import yaml

KINDS = {"spec", "state", "archive", "term", "note", "prob", "dec"}
LIVE_KINDS = {"spec", "state"}
STATUS_BY_KIND = {
    "spec": {"draft", "active", "superseded", "deprecated"},
    "state": {"draft", "active", "superseded", "deprecated"},
    "archive": {"landed", "superseded", "abandoned"},
    "term": {"draft", "active", "deprecated"},
}
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]*$|^[A-Z]{2,4}\.[a-z][a-z0-9-]*\.\d+$")
REF_RE = re.compile(r"^(spec|state|archive|term|note|prob|dec):[A-Za-z0-9._-]+$")
REQUIRED_COMMON = ["id", "kind", "subproject", "title", "owner",
                   "status", "last_reviewed", "applies_to", "tags",
                   "related", "indexed"]
REQUIRED_ARCHIVE = ["feature", "date", "produced", "superseded_by"]
SUBPROJECTS = {"umbrella", "server", "frontend", "game-app"}


@dataclass
class Issue:
    level: str  # "error" or "warn"
    path: Path
    msg: str

    def __str__(self) -> str:
        tag = "ERROR" if self.level == "error" else "warn"
        return f"[{tag}] {self.path}: {self.msg}"


@dataclass
class Doc:
    path: Path
    meta: dict
    body: str

    @property
    def id(self) -> str | None:
        return self.meta.get("id")

    @property
    def kind(self) -> str | None:
        return self.meta.get("kind")


@dataclass
class Registry:
    by_kind_id: dict[tuple[str, str], Doc] = field(default_factory=dict)

    def add(self, doc: Doc) -> None:
        if doc.kind and doc.id:
            self.by_kind_id[(doc.kind, doc.id)] = doc

    def resolves(self, ref: str) -> bool:
        if ":" not in ref:
            return False
        kind, doc_id = ref.split(":", 1)
        return (kind, doc_id) in self.by_kind_id


def parse_frontmatter_md(path: Path) -> Doc | None:
    """Parse a docs/**/*.md file with --- YAML frontmatter ---."""
    try:
        fm = frontmatter.load(path)
    except Exception as e:
        raise ValueError(f"frontmatter parse failure: {e}") from e
    if not fm.metadata:
        return None
    return Doc(path=path, meta=fm.metadata, body=fm.content)


def parse_haft_spec_md(path: Path) -> list[Doc]:
    """Parse .haft/specs/*.md files (haft's fenced ```yaml spec-section blocks)."""
    text = path.read_text()
    docs: list[Doc] = []
    for match in re.finditer(
        r"```yaml\s+spec-section\s*\n(.*?)\n```", text, re.DOTALL
    ):
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            continue
        if isinstance(meta, dict) and meta.get("id") and meta.get("kind"):
            docs.append(Doc(path=path, meta=meta, body=""))
    return docs


_HAFT_ID_RE = re.compile(r"^id:\s*(\S+)\s*$", re.MULTILINE)


def parse_haft_id_only(path: Path) -> Doc | None:
    """Lightweight haft-carrier id extraction.

    Haft's own writer doesn't always quote title strings, which makes them
    invalid YAML when the title contains a colon. We don't need the full
    frontmatter for haft carriers — only the `id` field for cross-ref
    resolution. Regex-extracting `id:` from the first --- block is robust
    against haft's title-quoting bug and matches haft's own slug format
    exactly.
    """
    try:
        text = path.read_text()
    except Exception:
        return None
    # Look only inside the first --- ... --- frontmatter block
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    block = text[:end]
    m = _HAFT_ID_RE.search(block)
    if not m:
        return None
    return Doc(path=path, meta={"id": m.group(1)}, body="")


HAFT_DIR_TO_KIND = {
    "notes": "note",
    "problems": "prob",
    "decisions": "dec",
    "solutions": "sol",
    "refresh": "ref",
}


def discover(root: Path) -> Iterable[tuple[Path, str, str | None]]:
    """Yield (path, source, kind_override) tuples.

    kind_override is the schema-native kind for haft carriers, derived from
    their parent directory. None for docs/ files (they declare their own
    kind in frontmatter).
    """
    for p in (root / "docs").rglob("*.md"):
        # Skip meta DIRECTORIES: any directory segment beginning with '_' (e.g.
        # docs/_templates/) holds authoring aids, not corpus documents. Only
        # directory segments count — a file like docs/specs/_schema-reference.md
        # is a real corpus doc (the leading underscore just sorts it first).
        rel = p.relative_to(root / "docs")
        if any(part.startswith("_") for part in rel.parts[:-1]):
            continue
        yield p, "docs", None
    haft = root / ".haft"
    if (haft / "specs").exists():
        for p in (haft / "specs").glob("*.md"):
            yield p, "haft-spec", "spec"
    for sub, kind in HAFT_DIR_TO_KIND.items():
        d = haft / sub
        if not d.exists():
            continue
        for p in d.glob("*.md"):
            yield p, "haft-simple", kind


def build_registry(root: Path) -> tuple[Registry, list[Issue]]:
    reg = Registry()
    issues: list[Issue] = []
    seen_ids: dict[tuple[str, str], Path] = {}
    for path, source, kind_override in discover(root):
        try:
            if source == "haft-spec":
                docs_in_file = parse_haft_spec_md(path)
            elif source == "haft-simple":
                # Lightweight extraction — haft carriers participate in the
                # cross-ref registry only, not in full schema validation.
                d = parse_haft_id_only(path)
                docs_in_file = [d] if d else []
            else:
                d = parse_frontmatter_md(path)
                docs_in_file = [d] if d else []
        except ValueError as e:
            issues.append(Issue("error", path, str(e)))
            continue
        for doc in docs_in_file:
            # For haft carriers, the schema-native kind comes from the
            # parent directory, NOT from the haft frontmatter (which uses
            # capitalized full names like "Note", "ProblemCard",
            # "DecisionRecord", "environment-change", etc.).
            if kind_override is not None:
                doc.meta = dict(doc.meta, kind=kind_override)
            kind = doc.kind
            doc_id = doc.id
            if kind and doc_id:
                key = (kind, doc_id)
                if key in seen_ids:
                    issues.append(Issue(
                        "error", path,
                        f"id '{doc_id}' (kind {kind}) duplicates "
                        f"earlier definition in {seen_ids[key]}",
                    ))
                seen_ids[key] = path
                reg.add(doc)
    return reg, issues


def validate_doc(doc: Doc, reg: Registry, corpus_root: Path | None = None) -> list[Issue]:
    issues: list[Issue] = []
    m = doc.meta

    # Required fields (common)
    for f in REQUIRED_COMMON:
        if f not in m:
            issues.append(Issue("error", doc.path,
                                f"missing required field '{f}'"))

    kind = m.get("kind")
    if kind not in KINDS:
        issues.append(Issue("error", doc.path,
                            f"kind '{kind}' not in {sorted(KINDS)}"))
        return issues  # downstream checks depend on kind

    # id slug
    if (i := m.get("id")) and not SLUG_RE.match(str(i)):
        issues.append(Issue("error", doc.path,
                            f"id '{i}' is not a valid slug "
                            f"(lowercase kebab-case or TS/ES dotted form)"))

    # status per kind
    status = m.get("status")
    allowed = STATUS_BY_KIND.get(kind, set())
    if status and status not in allowed:
        issues.append(Issue("error", doc.path,
                            f"status '{status}' not allowed for kind "
                            f"'{kind}'; allowed: {sorted(allowed)}"))

    # subproject
    sub = m.get("subproject")
    if sub and sub not in SUBPROJECTS:
        issues.append(Issue("error", doc.path,
                            f"subproject '{sub}' not in {sorted(SUBPROJECTS)}"))

    # indexed semantics
    indexed = m.get("indexed")
    if kind in LIVE_KINDS and indexed is False:
        issues.append(Issue("error", doc.path,
                            f"kind '{kind}' must have indexed: true"))
    if kind == "archive" and indexed is True:
        issues.append(Issue("error", doc.path,
                            "kind 'archive' must have indexed: false"))

    # archive-specific required fields
    if kind == "archive":
        for f in REQUIRED_ARCHIVE:
            if f not in m:
                issues.append(Issue("error", doc.path,
                                    f"archive entry missing required '{f}'"))

    # related: strict resolution
    for ref in m.get("related") or []:
        if not REF_RE.match(str(ref)):
            issues.append(Issue("error", doc.path,
                                f"related: '{ref}' has invalid format "
                                f"(expected '<kind>:<id>')"))
            continue
        if not reg.resolves(str(ref)):
            issues.append(Issue("error", doc.path,
                                f"related: '{ref}' does not resolve"))

    # applies_to reachability (skip for archive per the V2 constraint).
    # Per V2-nested architecture: applies_to globs are interpreted relative
    # to the CORPUS root passed via --root, NOT relative to the umbrella
    # (which may be a different repo entirely). When corpus_root is not
    # supplied, fall back to walking up to find a marker (.haft/ for the
    # umbrella case, otherwise the nearest docs/specs/ ancestor).
    if kind in LIVE_KINDS:
        if corpus_root is not None:
            root = corpus_root
        else:
            root = doc.path.resolve().parents[0]
            # Walk up to the project root. Markers are generic (a git repo, or the
            # memo-bank registry); `.haft/` is kept for projects that use it. Without
            # a generic marker the walk used to run past the project and resolve
            # applies_to against the home directory.
            markers = (".git", ".island-slices.json", ".haft")
            while root.parent != root:
                if any((root / m).exists() for m in markers):
                    break
                if (root / "docs" / "specs").is_dir() and root != doc.path.parent:
                    break
                root = root.parent
        for pattern in m.get("applies_to") or []:
            matches = list(root.glob(pattern))
            if not matches:
                issues.append(Issue("error", doc.path,
                                    f"applies_to: '{pattern}' matches "
                                    f"zero files under {root}"))

    return issues


def _json_serializable(value):
    """Coerce non-JSON-native types (e.g. dates) to strings."""
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, list):
        return [_json_serializable(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_serializable(v) for k, v in value.items()}
    return str(value)  # datetime, etc.


def build_index(root: Path, reg: Registry) -> dict:
    """Build a deterministic, JSON-serializable index of the corpus.

    The MCP memo-bank service reads this at startup (regenerable from
    frontmatter via `validator.py --index <path>`). Source of truth stays
    in the markdown files; the index is a cache.
    """
    docs_entries: list[dict] = []
    haft_entries: list[dict] = []

    for path, source, kind_override in discover(root):
        if source == "docs":
            try:
                doc = parse_frontmatter_md(path)
            except ValueError:
                continue
            if doc is None:
                continue
            m = doc.meta
            docs_entries.append({
                "id": m.get("id"),
                "kind": m.get("kind"),
                "subproject": m.get("subproject"),
                "title": m.get("title"),
                "status": m.get("status"),
                "owner": m.get("owner"),
                "path": str(path.relative_to(root)),
                "applies_to": _json_serializable(m.get("applies_to") or []),
                "tags": _json_serializable(m.get("tags") or []),
                "related": _json_serializable(m.get("related") or []),
                "indexed": m.get("indexed"),
                "last_reviewed": _json_serializable(m.get("last_reviewed")),
                "valid_until": _json_serializable(m.get("valid_until")),
            })
        elif source == "haft-spec":
            for doc in parse_haft_spec_md(path):
                haft_entries.append({
                    "kind": "spec",
                    "id": doc.id,
                    "title": doc.meta.get("title"),
                    "status": doc.meta.get("status"),
                    "path": str(path.relative_to(root)),
                })
        elif source == "haft-simple":
            d = parse_haft_id_only(path)
            if d and kind_override:
                haft_entries.append({
                    "kind": kind_override,
                    "id": d.id,
                    "path": str(path.relative_to(root)),
                })

    docs_entries.sort(key=lambda d: (d.get("kind") or "", d.get("id") or ""))
    haft_entries.sort(key=lambda d: (d.get("kind") or "", d.get("id") or ""))

    return {
        "version": 1,
        "schema": "schema-frontmatter-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stats": {
            "docs_count": len(docs_entries),
            "haft_count": len(haft_entries),
            "registered": len(reg.by_kind_id),
        },
        "docs": docs_entries,
        "haft_carriers": haft_entries,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default=".",
                        type=Path, help="repo root (default: cwd)")
    parser.add_argument("--quiet", action="store_true",
                        help="suppress warnings; only errors")
    parser.add_argument(
        "--index", metavar="PATH", type=Path, default=None,
        help="also write docs/index.json to PATH (only on validator pass)",
    )
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()

    reg, registry_issues = build_registry(root)
    issues = list(registry_issues)

    for path, source, _kind_override in discover(root):
        if source != "docs":
            continue  # Full schema check applies to docs/** only.
        try:
            doc = parse_frontmatter_md(path)
        except ValueError:
            continue  # already reported
        if doc is not None:
            issues.extend(validate_doc(doc, reg, corpus_root=root))

    errors = [i for i in issues if i.level == "error"]
    warnings = [i for i in issues if i.level == "warn"]

    for i in errors:
        print(i, file=sys.stderr)
    if not args.quiet:
        for i in warnings:
            print(i, file=sys.stderr)

    if errors:
        print(f"\n{len(errors)} error(s), {len(warnings)} warning(s)",
              file=sys.stderr)
        return 1
    print(f"OK: 0 errors, {len(warnings)} warnings, "
          f"{len(reg.by_kind_id)} docs registered", file=sys.stderr)

    if args.index is not None:
        index = build_index(root, reg)
        args.index.parent.mkdir(parents=True, exist_ok=True)
        args.index.write_text(
            json.dumps(index, indent=2, sort_keys=False, ensure_ascii=False)
            + "\n"
        )
        print(f"wrote {args.index} "
              f"({index['stats']['docs_count']} docs, "
              f"{index['stats']['haft_count']} haft carriers)",
              file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
