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
from pathlib import Path
from typing import Any

import yaml


class SpecSource:
    """Base adapter. Subclass and implement `detect` (+ `read_terms` if the
    artifact isn't a fenced yaml term-map)."""

    name: str = "unnamed"

    def detect(self, root: Path) -> list[Path]:
        """Artifact paths this source contributes under `root` (empty if absent)."""
        raise NotImplementedError

    def read_terms(self, root: Path) -> list[dict[str, Any]]:
        """Term entries: {term, domain, definition, source}. Default: none."""
        return []


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


# Registration order IS precedence.
_SOURCES: list[SpecSource] = [HaftSource(), DocsNativeSource()]


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
