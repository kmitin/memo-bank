"""Spec-source adapters — pluggable readers for external spec/vocabulary ecosystems.

A project's governing vocabulary usually already lives in whatever methodology the
project uses. Rather than hardcode a list of known paths in the engine, each
ecosystem is a **source adapter**: it knows how to detect its own artifacts under
a project root and parse them into the engine's neutral shape.

Supporting a new ecosystem is therefore a new adapter — a small class, registered
— never an edit to engine internals:

    from spec_sources import FencedTermMapSource, register_source

    class MyMethodSource(FencedTermMapSource):
        name = "my-method"
        rel_paths = (Path("my-method") / "glossary.md",)

    register_source(MyMethodSource())

Adapters that need different parsing subclass `SpecSource` directly and implement
`detect()` / `read_terms()`. Precedence is registration order: when two sources
define the same term, the earlier-registered adapter wins.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Artifact:
    """A governing document contributed by a methodology.

    `governs` are repo-relative globs the artifact claims authority over. Most
    ecosystems don't declare them, so it is usually derived from the file paths
    the document mentions — which is why the drift check can work over foreign
    artifacts at all.
    """
    path: Path
    source: str
    kind: str                      # glossary | decision | design | plan | spec
    governs: tuple[str, ...] = ()


class SpecSource:
    """Base adapter. Subclass and implement `detect`; add `read_terms` and/or
    `artifacts` depending on what the ecosystem contributes."""

    name: str = "unnamed"
    artifact_kind: str = "spec"

    def detect(self, root: Path) -> list[Path]:
        """Artifact paths this source contributes under `root` (empty if absent)."""
        raise NotImplementedError

    def read_terms(self, root: Path) -> list[dict[str, Any]]:
        """Term entries: {term, domain, definition, source}. Default: none."""
        return []

    def artifacts(self, root: Path) -> list[Artifact]:
        """Governing documents this source contributes, for drift/coverage.

        Default: every detected file, governing the paths it mentions."""
        return [
            Artifact(path=p, source=self.name, kind=self.artifact_kind,
                     governs=tuple(mentioned_paths(p)))
            for p in self.detect(root)
        ]


# Looks like a source path: has a directory separator and a file extension.
_PATH_MENTION = re.compile(r"[`\"'(\s]([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.[A-Za-z0-9]{1,6})")


def mentioned_paths(path: Path, *, limit: int = 200) -> list[str]:
    """Repo-relative-looking file paths referenced inside a document.

    Ecosystems like ADRs or design docs don't declare `applies_to`; what they DO
    is name the files they are about. Treating those mentions as the governed set
    lets the drift check reason about foreign artifacts without inventing a
    convention for them."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    seen: dict[str, None] = {}
    for m in _PATH_MENTION.finditer(text):
        candidate = m.group(1)
        if not candidate.startswith(("http", "www.")):
            seen.setdefault(candidate, None)
        if len(seen) >= limit:
            break
    return list(seen)


class FencedTermMapSource(SpecSource):
    """Terms held in a fenced ```yaml term-map block with an `entries:` list.

    Covers any ecosystem that keeps a markdown glossary in that shape — set
    `rel_paths` to where the ecosystem puts it.
    """

    rel_paths: tuple[Path, ...] = ()

    def detect(self, root: Path) -> list[Path]:
        return [root / rel for rel in self.rel_paths if (root / rel).exists()]

    def read_terms(self, root: Path) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for path in self.detect(root):
            text = path.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"```yaml term-map\n(.*?)```", text, re.DOTALL)
            if not m:
                continue
            try:
                data = yaml.safe_load(m.group(1)) or {}
            except Exception:  # noqa: BLE001 — a malformed glossary must not break serving
                continue
            for e in data.get("entries", []) or []:
                if isinstance(e, dict) and e.get("term"):
                    out.append({
                        "term": str(e["term"]),
                        "domain": e.get("domain"),
                        "definition": (e.get("definition") or "").strip(),
                        "source": self.name,
                    })
        return out


class HaftSource(FencedTermMapSource):
    """haft — an engineering-decision graph; its term map is the bounded-context
    vocabulary carrier."""
    name = "haft"
    rel_paths = (Path(".haft") / "specs" / "term-map.md",)


class DocsNativeSource(FencedTermMapSource):
    """The memo-bank's own location, for projects using no other methodology.
    Underscore-prefixed so the corpus loader skips it as a document."""
    name = "docs-native"
    rel_paths = (Path("docs") / "_terms" / "term-map.md",)


class GrillWithDocsSource(SpecSource):
    """grill-with-docs — interviews a design and writes it down as it resolves.

    Glossary lands in a root `CONTEXT.md` (or per-context `CONTEXT.md` files when
    the repo carries a `CONTEXT-MAP.md`); decisions land in `docs/adr/`.
    """
    name = "grill-with-docs"
    artifact_kind = "decision"

    def detect(self, root: Path) -> list[Path]:
        found = [p for p in (root / "CONTEXT.md", root / "CONTEXT-MAP.md") if p.exists()]
        adr = root / "docs" / "adr"
        if adr.is_dir():
            found.extend(sorted(adr.glob("*.md")))
        if (root / "CONTEXT-MAP.md").exists():          # per-context glossaries
            found.extend(p for p in sorted(root.glob("*/CONTEXT.md")))
        return found

    def read_terms(self, root: Path) -> list[dict[str, Any]]:
        """CONTEXT.md glossaries are prose, not a fenced block: read `**term** —
        definition` / `- term: definition` lines."""
        out: list[dict[str, Any]] = []
        for p in self.detect(root):
            if p.name != "CONTEXT.md":
                continue
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(r"\s*[-*]?\s*\*\*(.+?)\*\*\s*[—:-]\s*(.+)", line)
                if m:
                    out.append({"term": m.group(1).strip(), "domain": None,
                                "definition": m.group(2).strip(), "source": self.name})
        return out

    def artifacts(self, root: Path) -> list[Artifact]:
        return [
            Artifact(path=p, source=self.name,
                     kind="glossary" if p.name in ("CONTEXT.md", "CONTEXT-MAP.md") else "decision",
                     governs=tuple(mentioned_paths(p)))
            for p in self.detect(root)
        ]


class SuperpowersSource(SpecSource):
    """superpowers — its brainstorming/planning workflows write designs and plans
    into the consuming repo under `docs/superpowers/`."""
    name = "superpowers"
    artifact_kind = "design"

    def detect(self, root: Path) -> list[Path]:
        base = root / "docs" / "superpowers"
        if not base.is_dir():
            return []
        return sorted(p for sub in ("specs", "plans") for p in (base / sub).glob("*.md"))

    def artifacts(self, root: Path) -> list[Artifact]:
        return [
            Artifact(path=p, source=self.name,
                     kind="plan" if p.parent.name == "plans" else "design",
                     governs=tuple(mentioned_paths(p)))
            for p in self.detect(root)
        ]


# Registration order IS precedence.
_SOURCES: list[SpecSource] = [
    HaftSource(),
    DocsNativeSource(),
    GrillWithDocsSource(),
    SuperpowersSource(),
]


def register_source(source: SpecSource, *, first: bool = False) -> None:
    """Add an adapter. `first=True` gives it precedence over the built-ins."""
    _SOURCES.insert(0, source) if first else _SOURCES.append(source)


def sources() -> list[SpecSource]:
    """Registered adapters, in precedence order."""
    return list(_SOURCES)


def detect_all(root: Path) -> list[tuple[str, Path]]:
    """(source name, artifact path) for every adapter that finds artifacts."""
    found: list[tuple[str, Path]] = []
    for src in _SOURCES:
        for path in src.detect(root):
            found.append((src.name, path))
    return found


def load_terms(root: Path) -> list[dict[str, Any]]:
    """Terms merged across every detected source; first source wins on duplicates."""
    merged: dict[str, dict[str, Any]] = {}
    for src in _SOURCES:
        for entry in src.read_terms(root):
            merged.setdefault(entry["term"], entry)
    return list(merged.values())


def artifact_paths(root: Path) -> list[Path]:
    """Every detected artifact path — used for reload/staleness signatures."""
    return [p for _name, p in detect_all(root)]


def all_artifacts(root: Path) -> list[Artifact]:
    """Governing artifacts from every registered source — what drift checks."""
    return [a for src in _SOURCES for a in src.artifacts(root)]
