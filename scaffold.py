"""`memobank init` — prepare a project to use the memo-bank.

The engine is a separate, installed package; a project holds only what is
genuinely *its own*: the slice/config registry, its corpus, its AGENTS.md, and
the authoring templates. No engine code is copied into the project, so there is
no forked engine to age independently — the version drift that bit a second
adopter and motivated packaging.

What this writes into the target:
  .island-slices.json                   project config (slices, source_globs, schema)
  AGENTS.md                             from the template — both agent loops baked in
  <slice>/docs/{specs,state,archive}/   the corpus skeleton, per slice
  docs/_templates/spec-template.md      authoring template (validator-skipped)
  docs/specs/schema-frontmatter-v1.md   the canonical schema (a corpus doc the project owns)
  Makefile                              thin targets that call the installed `memobank`
  .mcp.json                             merged — never clobbers other servers
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # the engine repo root
TEMPLATES = HERE / "templates"

# Engine asset -> where it belongs in the target. These become the project's own
# content once written (it authors against them); the engine only seeds them.
ASSETS: dict[str, str] = {
    "spec-template.md": "docs/_templates/spec-template.md",
    "schema-frontmatter-v1.md": "docs/specs/schema-frontmatter-v1.md",
}

MAKEFILE = """\
# memo-bank targets — the engine is the installed `memobank` package, not vendored.
.PHONY: validate index coverage-loop drift-check serve

validate: ## frontmatter + cross-ref validator over the corpus
\tmemobank validate . --index docs/index.json

index: ## regenerate docs/index.json
\tmemobank validate . --index docs/index.json

coverage-loop: ## demand-driven coverage loop — uncovered changed code (non-blocking)
\tmemobank coverage --mode staged

drift-check: ## stale-spec drift — governed code changed since last_reviewed (non-blocking)
\tmemobank drift --registry .island-slices.json

serve: ## run the read-only MCP server
\tmemobank serve --federation .island-slices.json
"""


def render_registry(island: str, slices: list[tuple[str, str]],
                    source_globs: list[str] | None = None,
                    eval_refs: dict | None = None, schema: str | None = None) -> str:
    d: dict = {
        "island": island,
        "version": 1,
        "slices": [{"name": n, "root": r} for n, r in slices],
        "source_globs": source_globs or ["src/**"],
        "schema": schema or "docs/specs/schema-frontmatter-v1.md",
    }
    if eval_refs:
        d["eval"] = eval_refs
    return json.dumps(d, indent=2) + "\n"


def render_agents(template_text: str, island: str, slices: list[tuple[str, str]]) -> str:
    body = template_text
    if body.lstrip().startswith("<!--"):          # drop the template's instruction header
        body = body.split("-->", 1)[1].lstrip("\n")
    body = body.replace("REPLACE-project-name", island)
    rows = "\n".join(f"| {n} | `{r}` | (describe {n}) |" for n, r in slices)
    body = body.replace("| REPLACE | REPLACE (repo-relative root) | REPLACE |", rows)
    body = body.replace("REPLACE", "TODO")        # remaining prose blocks → TODO for the human
    return body


def render_mcp(existing: str | None, target: Path) -> str:
    """Merge the memo-bank server into an existing .mcp.json (never clobber others).

    Points at the installed console script, so the project carries no engine path."""
    data = json.loads(existing) if existing else {}
    servers = data.setdefault("mcpServers", {})
    servers.setdefault("memo-bank", {
        "command": "memobank",
        "args": ["serve", "--federation", str(target / ".island-slices.json")],
    })
    return json.dumps(data, indent=2) + "\n"


def render_makefile() -> str:
    return MAKEFILE


def scaffold_docs(target: Path, slices: list[tuple[str, str]]) -> None:
    for _, root in slices:
        for sub in ("specs", "state", "archive"):
            d = target / root / "docs" / sub
            d.mkdir(parents=True, exist_ok=True)
            (d / ".gitkeep").touch()


def plan_writes(slices: list[tuple[str, str]]) -> list[str]:
    """Target-relative paths this scaffold creates — config + docs only, no engine."""
    out = [".island-slices.json", "AGENTS.md", "Makefile", ".mcp.json", *ASSETS.values()]
    for _, root in slices:
        for sub in ("specs", "state", "archive"):
            out.append(f"{(Path(root) / 'docs' / sub).as_posix()}/")
    return out


def run(target, island: str, slices: list[tuple[str, str]],
        source_globs: list[str] | None = None, dry_run: bool = False,
        force: bool = False, engine: Path | None = None) -> int:
    target = Path(target)
    templates = (Path(engine) if engine else HERE) / "templates"

    if dry_run:
        print(f"[dry-run] prepare {target} for the memo-bank ({len(slices)} slice(s)) "
              f"— no engine code is copied:", file=sys.stderr)
        for rel in plan_writes(slices):
            print(f"  + {rel}", file=sys.stderr)
        return 0

    def _write(rel: str, text: str) -> None:
        p = target / rel
        if p.exists() and not force:
            print(f"  skip (exists): {rel}", file=sys.stderr)
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    _write(".island-slices.json", render_registry(island, slices, source_globs))
    _write("AGENTS.md", render_agents((templates / "AGENTS.template.md").read_text(),
                                      island, slices))
    _write("Makefile", render_makefile())
    scaffold_docs(target, slices)
    for asset, dest in ASSETS.items():
        p = target / dest
        if p.exists() and not force:
            print(f"  skip (exists): {dest}", file=sys.stderr)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(templates / asset, p)

    mcp = target / ".mcp.json"
    mcp.write_text(render_mcp(mcp.read_text() if mcp.exists() else None, target))

    print(f"prepared {target} — the engine stays in the installed package. Next:", file=sys.stderr)
    print("  memobank validate . --index docs/index.json", file=sys.stderr)
    print("  memobank coverage --mode staged && memobank drift --registry .island-slices.json", file=sys.stderr)
    print("  author docs/{specs,state,archive}/ against docs/_templates/spec-template.md", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", type=Path, required=True)
    ap.add_argument("--island", required=True)
    ap.add_argument("--slice", action="append", default=[], metavar="name=root",
                    help="repeatable; e.g. --slice umbrella=. --slice api=services/api")
    ap.add_argument("--source-glob", action="append", default=[], dest="source_globs")
    ap.add_argument("--engine", type=Path, default=None,
                    help="engine repo to read templates from (default: this install)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    slices = [(s.split("=", 1)[0], s.split("=", 1)[1]) for s in args.slice] or [("umbrella", ".")]
    return run(args.target, args.island, slices, source_globs=args.source_globs or None,
               dry_run=args.dry_run, force=args.force, engine=args.engine)


if __name__ == "__main__":
    raise SystemExit(main())
