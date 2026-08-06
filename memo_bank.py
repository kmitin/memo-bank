#!/usr/bin/env python3
"""Memo-bank MCP server v1 — read-only docs corpus surface (MCP).

The memo-bank wraps the V2-nested docs corpus (per architectural decision
dec-20260517-the V2-nested docs decision)
as an MCP tool surface. It is intentionally read-only and stateless:
authoring stays in editors + git workflow; the memo-bank only serves
queries.

V1 scope:
    - Three tools fully implemented: docs.list, docs.get, docs.resolve_path
    - Five tools stubbed: docs.get_section, docs.search_live,
      docs.search_archive, docs.resolve_term, docs.compose_context
    - Multi-corpus support: pass --corpus name=path per subproject.
    - Corpus loading prefers docs/index.json (validator-generated), falls
      back to filesystem walk + frontmatter parse.
    - No reload mechanism. Restart the server to pick up corpus changes.

V1 explicitly does NOT include:
    - Writes (read-only first, per the V2 mcp_deployment_independence
      and corpus_subsumption constraints; authoring stays in git).
    - Webhook or poll-based reload.
    - Cross-repo cross-reference resolution (each corpus validates
      independently; cross-corpus `related:` references are a v2
      concern surfaced via the global index merge).
    - Health endpoint with staleness detection.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import frontmatter
import yaml
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

LOG = logging.getLogger("memo-bank")

# ---------------------------------------------------------------------------
# Corpus loading
# ---------------------------------------------------------------------------

DOC_KINDS_LIVE = {"spec", "state"}
DOC_KIND_ARCHIVE = "archive"


@dataclass
class CorpusDoc:
    """A single doc registered in the memo-bank, sourced from one corpus."""
    id: str
    kind: str
    subproject: str
    title: str
    corpus_name: str        # which corpus (umbrella, server, frontend, game-app, ...)
    corpus_root: Path       # absolute path to the corpus root
    rel_path: str           # path RELATIVE to corpus_root
    applies_to: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    status: str | None = None
    owner: str | None = None
    indexed: bool = True
    last_reviewed: str | None = None

    @property
    def abs_path(self) -> Path:
        return self.corpus_root / self.rel_path

    def to_summary(self) -> dict[str, Any]:
        """Minimal projection for list / search responses."""
        return {
            "id": self.id,
            "kind": self.kind,
            "subproject": self.subproject,
            "title": self.title,
            "corpus": self.corpus_name,
            "path": self.rel_path,
            "tags": list(self.tags),
            "indexed": self.indexed,
        }


@dataclass
class Corpus:
    """All docs across all configured corpora, addressable by id."""
    by_id: dict[str, CorpusDoc] = field(default_factory=dict)
    corpora: dict[str, Path] = field(default_factory=dict)  # name -> root
    terms: dict[str, list[dict[str, Any]]] = field(default_factory=dict)  # term -> entries

    def docs(self) -> list[CorpusDoc]:
        return list(self.by_id.values())


class DuplicateIdError(ValueError):
    """Raised when two docs in one corpus load share an id — a corpus bug
    (silent overwrite at ingestion is the hazard the eval harness surfaced)."""


def corpus_signature(root: Path) -> str:
    """A cheap content signature for a corpus root — sorted (path, mtime_ns)
    over the inputs that affect what's served. Used for staleness/reload."""
    import hashlib
    parts: list[str] = []
    docs = root / "docs"
    if docs.exists():
        for p in sorted(docs.rglob("*.md")):
            rel = p.relative_to(docs)
            if any(seg.startswith("_") for seg in rel.parts[:-1]):
                continue
            parts.append(f"{p}:{p.stat().st_mtime_ns}")
    extras = [root / "docs" / "index.json"]
    tm = _term_map_path(root)          # either supported location; reload tracks both
    if tm is not None:
        extras.append(tm)
    for extra in extras:
        if extra.exists():
            parts.append(f"{extra}:{extra.stat().st_mtime_ns}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


# Term-map sources, in precedence order. `.haft/` is supported for projects that
# use haft (that was the original, coupled binding); `docs/_terms/` is the
# docs-native location so the engine carries NO dependency on a separate tool —
# an adopter without haft still gets resolve_term. Underscore-prefixed, so the
# corpus loader skips it as a document.
_TERM_MAP_CANDIDATES = (
    Path(".haft") / "specs" / "term-map.md",
    Path("docs") / "_terms" / "term-map.md",
)


def _term_map_path(root: Path) -> Path | None:
    """First existing term-map source under `root`, or None."""
    for rel in _TERM_MAP_CANDIDATES:
        p = root / rel
        if p.exists():
            return p
    return None


def _load_terms(root: Path) -> list[dict[str, Any]]:
    """Parse term-map entries from the project's term map (a fenced
    ```yaml term-map block with an `entries:` list).

    Terms are bounded-context vocabulary, distinct from free `tags`. The source
    is resolved from `_TERM_MAP_CANDIDATES` so the engine works with or without
    haft; with no source, resolve_term simply reports `absent`."""
    tm = _term_map_path(root)
    if tm is None:
        return []
    text = tm.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"```yaml term-map\n(.*?)```", text, re.DOTALL)
    if not m:
        return []
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    for e in data.get("entries", []) or []:
        if isinstance(e, dict) and e.get("term"):
            out.append({"term": str(e["term"]),
                        "domain": e.get("domain"),
                        "definition": (e.get("definition") or "").strip()})
    return out


def _coerce_str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [str(v)]


def _doc_from_index_entry(
    entry: dict[str, Any], corpus_name: str, corpus_root: Path
) -> CorpusDoc | None:
    """Build a CorpusDoc from one entry in docs/index.json."""
    if not entry.get("id") or not entry.get("kind"):
        return None
    return CorpusDoc(
        id=str(entry["id"]),
        kind=str(entry["kind"]),
        subproject=str(entry.get("subproject") or corpus_name),
        title=str(entry.get("title") or entry["id"]),
        corpus_name=corpus_name,
        corpus_root=corpus_root,
        rel_path=str(entry.get("path") or ""),
        applies_to=_coerce_str_list(entry.get("applies_to")),
        tags=_coerce_str_list(entry.get("tags")),
        related=_coerce_str_list(entry.get("related")),
        status=entry.get("status"),
        owner=entry.get("owner"),
        indexed=bool(entry.get("indexed", True)),
        last_reviewed=entry.get("last_reviewed"),
    )


def _doc_from_markdown(
    path: Path, corpus_name: str, corpus_root: Path
) -> CorpusDoc | None:
    """Fallback path: parse a docs/**/*.md directly when no index.json."""
    try:
        doc = frontmatter.load(str(path))
    except Exception as exc:  # noqa: BLE001
        LOG.warning("skipping %s (frontmatter parse failed: %s)", path, exc)
        return None
    m = doc.metadata
    if not m.get("id") or not m.get("kind"):
        return None
    rel = path.relative_to(corpus_root).as_posix()
    return CorpusDoc(
        id=str(m["id"]),
        kind=str(m["kind"]),
        subproject=str(m.get("subproject") or corpus_name),
        title=str(m.get("title") or m["id"]),
        corpus_name=corpus_name,
        corpus_root=corpus_root,
        rel_path=rel,
        applies_to=_coerce_str_list(m.get("applies_to")),
        tags=_coerce_str_list(m.get("tags")),
        related=_coerce_str_list(m.get("related")),
        status=m.get("status"),
        owner=m.get("owner"),
        indexed=bool(m.get("indexed", True)),
        last_reviewed=m.get("last_reviewed"),
    )


def load_corpus(corpora_arg: list[tuple[str, Path]],
                strict_ids: bool = True) -> Corpus:
    """Load all configured corpora into a single in-memory namespace.

    For each corpus, prefer docs/index.json (fast). Fall back to walking
    docs/**/*.md and parsing frontmatter (slow, but always works). Also loads
    term-map entries per root for resolve_term.

    strict_ids (default True): a duplicate id within one load is a HARD ERROR
    (DuplicateIdError), not a silent keep-first — silent overwrite at ingestion
    is a real corpus hazard (the eval harness surfaced it via colliding
    README.md files). Pass strict_ids=False to fall back to warn-and-keep-first.
    """
    corpus = Corpus()

    def add(d: CorpusDoc | None) -> int:
        if d is None:
            return 0
        if d.id in corpus.by_id:
            prior = corpus.by_id[d.id]
            if strict_ids:
                raise DuplicateIdError(
                    f"duplicate id '{d.id}': {prior.corpus_name}:{prior.rel_path} "
                    f"and {d.corpus_name}:{d.rel_path}")
            LOG.warning("duplicate id '%s'; keeping first (%s)", d.id, prior.rel_path)
            return 0
        corpus.by_id[d.id] = d
        return 1

    for name, root in corpora_arg:
        root = root.resolve()
        if not root.exists():
            LOG.warning("corpus '%s' root does not exist: %s", name, root)
            continue
        corpus.corpora[name] = root
        for e in _load_terms(root):
            corpus.terms.setdefault(e["term"], []).append({**e, "corpus": name})
        index_path = root / "docs" / "index.json"
        if index_path.exists():
            try:
                index = json.loads(index_path.read_text())
                count = sum(add(_doc_from_index_entry(e, name, root))
                            for e in index.get("docs", []))
                LOG.info("loaded corpus '%s' from index.json: %d docs", name, count)
                continue
            except DuplicateIdError:
                raise
            except Exception as exc:  # noqa: BLE001
                LOG.warning("index.json read failed for corpus '%s', "
                            "falling back to walk: %s", name, exc)
        docs_dir = root / "docs"
        if not docs_dir.exists():
            LOG.warning("corpus '%s' has no docs/ at %s", name, root)
            continue
        count = 0
        for path in sorted(docs_dir.rglob("*.md")):
            # Skip meta dirs (docs/_templates/ etc.): underscore-prefixed
            # DIRECTORY segments only (a file like _schema-reference.md is real).
            rel = path.relative_to(docs_dir)
            if any(part.startswith("_") for part in rel.parts[:-1]):
                continue
            count += add(_doc_from_markdown(path, name, root))
        LOG.info("loaded corpus '%s' from filesystem walk: %d docs", name, count)
    return corpus


# ---------------------------------------------------------------------------
# Tool implementations (pure functions over Corpus)
# ---------------------------------------------------------------------------

def tool_list(
    corpus: Corpus, subproject: str | None = None, kind: str | None = None,
    include_archive: bool = False,
) -> list[dict[str, Any]]:
    """docs.list — return summaries for docs matching the filters."""
    out: list[dict[str, Any]] = []
    for d in corpus.docs():
        if not include_archive and d.kind == DOC_KIND_ARCHIVE:
            continue
        if subproject is not None and d.subproject != subproject:
            continue
        if kind is not None and d.kind != kind:
            continue
        out.append(d.to_summary())
    out.sort(key=lambda s: (s["kind"], s["subproject"], s["id"]))
    return out


def tool_get(corpus: Corpus, id: str) -> dict[str, Any]:
    """docs.get — return full doc {frontmatter, body, path, corpus}."""
    d = corpus.by_id.get(id)
    if d is None:
        return {"error": f"id '{id}' not found", "available_count": len(corpus.by_id)}
    try:
        loaded = frontmatter.load(str(d.abs_path))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"failed to read {d.abs_path}: {exc}"}
    return {
        "id": d.id,
        "kind": d.kind,
        "subproject": d.subproject,
        "title": d.title,
        "corpus": d.corpus_name,
        "path": d.rel_path,
        "frontmatter": dict(loaded.metadata),
        "body": loaded.content,
    }


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Convert a glob with `**` semantics into a compiled regex.

    Semantics:
        `**` (alone in a path segment) matches zero or more path segments.
        `*` matches any characters within one path segment (not /).
        `?` matches a single character (not /).
        All other characters match literally.
    """
    pattern = pattern.replace("\\", "/").lstrip("/")
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                # ** — match zero or more path segments
                if i + 2 < n and pattern[i + 2] == "/":
                    out.append(r"(?:.*/)?")
                    i += 3
                else:
                    out.append(r".*")
                    i += 2
            else:
                # single * — match within one segment (not /)
                out.append(r"[^/]*")
                i += 1
        elif c == "?":
            out.append(r"[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def _glob_matches(pattern: str, target: str) -> bool:
    """Match a doc.applies_to glob against a target POSIX path."""
    target = target.replace("\\", "/").lstrip("/")
    try:
        return _glob_to_regex(pattern).match(target) is not None
    except re.error:
        return False


def _specificity(pattern: str) -> tuple[int, int, int]:
    """Score a glob pattern by specificity. Higher = more specific.

    Components:
      (segment_count, literal_chars, -wildcard_penalty)
    """
    pattern = pattern.replace("\\", "/")
    segs = [s for s in pattern.split("/") if s]
    literal = sum(len(s) for s in segs if "*" not in s and "?" not in s)
    wildcards = sum(s.count("*") + s.count("?") + s.count("[") for s in segs)
    return (len(segs), literal, -wildcards)


def tool_resolve_path(corpus: Corpus, path: str) -> list[dict[str, Any]]:
    """docs.resolve_path — return docs whose applies_to globs match `path`.

    Results are ordered by specificity of the matching glob (most specific
    first); within the same specificity, by subproject + id for stable order.
    """
    matches: list[tuple[tuple[int, int, int], CorpusDoc, str]] = []
    norm = path.replace("\\", "/").lstrip("/")
    for d in corpus.docs():
        if d.kind == DOC_KIND_ARCHIVE:
            continue  # archive entries don't "govern" current code
        for pattern in d.applies_to:
            if _glob_matches(pattern, norm):
                matches.append((_specificity(pattern), d, pattern))
                break
    matches.sort(key=lambda t: (
        -t[0][0], -t[0][1], -t[0][2], t[1].subproject, t[1].id
    ))
    return [
        {**d.to_summary(), "matched_glob": pat}
        for (_score, d, pat) in matches
    ]


# ---------------------------------------------------------------------------
# Incremental-load core — the rungs that deliver "load just enough, not at
# once" (the environment-change spec observables 6, 12, 13):
#   get_section   one H2 slice of one doc          (rung 1)
#   search_live   find by content, hot corpus      (rung 3, archive excluded)
#   search_archive find by content, cold corpus    (rung 3, archive only)
#   compose_context budget-bounded assembly         (rung 4, the capstone)
# All model-independent: lexical scoring + a char/4 token estimate, no embeddings.
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9]+")


def _load_body(d: CorpusDoc) -> str | None:
    try:
        return frontmatter.load(str(d.abs_path)).content
    except Exception:  # noqa: BLE001
        return None


def _split_sections(body: str) -> list[tuple[str, str]]:
    """Split a markdown body into (heading, section_text) by H2 ('## ').

    Content before the first H2 is returned under heading '' (preamble).
    The H1 title line is treated as preamble. Section_text includes the
    heading line, so a returned section is a self-contained block.
    """
    lines = body.splitlines(keepends=True)
    sections: list[tuple[str, list[str]]] = [("", [])]
    for ln in lines:
        if ln.startswith("## "):
            sections.append((ln[3:].strip(), [ln]))
        else:
            sections[-1][1].append(ln)
    return [(h, "".join(buf).strip()) for h, buf in sections if "".join(buf).strip()]


def _estimate_tokens(text: str) -> int:
    """Model-independent token estimate. ~4 chars/token, floor 1."""
    return max(1, len(text) // 4)


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _lexical_score(query_terms: set[str], d: CorpusDoc, body: str) -> tuple[float, str]:
    """Weighted term-overlap score (title*3 + tags*2 + body*1), plus a snippet.

    Model-independent. Returns (score, first-matching-line snippet).
    """
    if not query_terms:
        return (0.0, "")
    title_toks = set(_tokenize(d.title))
    tag_toks = set(_tokenize(" ".join(d.tags)))
    body_toks = _tokenize(body)
    body_set = set(body_toks)
    score = (
        3.0 * len(query_terms & title_toks)
        + 2.0 * len(query_terms & tag_toks)
        + 1.0 * len(query_terms & body_set)
    )
    snippet = ""
    for line in body.splitlines():
        lt = set(_tokenize(line))
        if query_terms & lt:
            snippet = line.strip()[:200]
            break
    return (score, snippet)


def tool_get_section(corpus: Corpus, id: str, section: str) -> dict[str, Any]:
    """docs.get_section — one H2 section of one doc (rung 1).

    Matching is case-insensitive; exact heading first, then prefix/substring.
    On miss, returns the available section headings so the caller can pick —
    itself an incremental step rather than a dead end."""
    d = corpus.by_id.get(id)
    if d is None:
        return {"error": f"id '{id}' not found"}
    body = _load_body(d)
    if body is None:
        return {"error": f"failed to read {d.abs_path}"}
    sections = _split_sections(body)
    want = section.strip().lower()
    exact = [(h, t) for h, t in sections if h.lower() == want]
    loose = [(h, t) for h, t in sections if want and want in h.lower()]
    hit = (exact or loose)
    if not hit:
        return {
            "id": id, "section": section, "found": False,
            "available_sections": [h for h, _ in sections if h],
        }
    heading, text = hit[0]
    return {
        "id": id, "corpus": d.corpus_name, "title": d.title,
        "section": heading, "found": True,
        "body": text, "tokens": _estimate_tokens(text),
    }


def _search(corpus: Corpus, query: str, *, archive_only: bool,
            subproject: str | None = None, kind: str | None = None,
            feature: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """Shared lexical search. Returns ranked POINTERS (summaries + score +
    snippet) — never full bodies, so search itself stays incremental."""
    qterms = set(_tokenize(query))
    scored: list[tuple[float, dict[str, Any]]] = []
    for d in corpus.docs():
        is_archive = d.kind == DOC_KIND_ARCHIVE
        if archive_only and not is_archive:
            continue
        if not archive_only and is_archive:
            continue
        if subproject is not None and d.subproject != subproject:
            continue
        if kind is not None and d.kind != kind:
            continue
        body = _load_body(d) or ""
        score, snippet = _lexical_score(qterms, d, body)
        if score <= 0:
            continue
        scored.append((score, {**d.to_summary(), "score": score, "snippet": snippet}))
    scored.sort(key=lambda t: (-t[0], t[1]["subproject"], t[1]["id"]))
    return [s for _, s in scored[:limit]]


def tool_search_live(corpus: Corpus, query: str, subproject: str | None = None,
                     kind: str | None = None) -> list[dict[str, Any]]:
    """docs.search_live — content search over HOT docs (specs + state).
    Archive is excluded by construction (distinct tool: search_archive)."""
    return _search(corpus, query, archive_only=False,
                   subproject=subproject, kind=kind)


def tool_search_archive(corpus: Corpus, query: str, feature: str | None = None,
                        subproject: str | None = None) -> list[dict[str, Any]]:
    """docs.search_archive — content search over COLD archive only."""
    out = _search(corpus, query, archive_only=True, subproject=subproject)
    if feature:
        f = feature.lower()
        out = [r for r in out if f in (r.get("id", "") + " " + r.get("title", "")).lower()]
    return out


def _compose_candidates(corpus: Corpus, question: str | None, path: str | None,
                        tags: list[str] | None) -> list[CorpusDoc]:
    """Gather candidate docs for composition, in precedence order:
    path matches (closest-wins) → tag intersection → question search.
    Dedupe preserving first occurrence."""
    ordered: list[str] = []
    if path:
        for s in tool_resolve_path(corpus, path):
            ordered.append(s["id"])
    if tags:
        tagset = {t.lower() for t in tags}
        for d in corpus.docs():
            if d.kind != DOC_KIND_ARCHIVE and tagset & {t.lower() for t in d.tags}:
                ordered.append(d.id)
    if question:
        for s in tool_search_live(corpus, question):
            ordered.append(s["id"])
    seen: set[str] = set()
    out: list[CorpusDoc] = []
    for did in ordered:
        if did in seen or did not in corpus.by_id:
            continue
        seen.add(did)
        out.append(corpus.by_id[did])
    return out


def tool_compose_context(corpus: Corpus, question: str | None = None,
                         path: str | None = None, tags: list[str] | None = None,
                         budget_tokens: int = 8000) -> dict[str, Any]:
    """docs.compose_context — the capstone (rung 4).

    Assemble a question-scoped context bundle under a token budget: one chosen
    section per candidate doc (the question-relevant section, else the first
    substantive section), added in precedence order until the budget is hit,
    plus first-degree `related:` POINTERS. If not even the top candidate's
    chosen section fits, degrade to a precedence-ranked ID list — NEVER a
    mid-section truncation, NEVER a whole-corpus dump."""
    candidates = _compose_candidates(corpus, question, path, tags)
    if not candidates:
        return {"mode": "id_list", "query": {"question": question, "path": path,
                "tags": tags}, "budget_tokens": budget_tokens,
                "candidates": [], "reason": "no candidates matched"}

    qterms = set(_tokenize(question)) if question else set()

    def choose_section(d: CorpusDoc) -> tuple[str, str]:
        body = _load_body(d) or ""
        secs = _split_sections(body)
        named = [(h, t) for h, t in secs if h] or secs
        if qterms:
            best = max(named, key=lambda ht: _lexical_score(qterms, d, ht[1])[0])
            return best
        return named[0]

    assembled: list[dict[str, Any]] = []
    related: list[dict[str, str]] = []
    omitted: list[str] = []
    used = 0
    overflow = False
    for d in candidates:
        if overflow:
            omitted.append(d.id)
            continue
        heading, text = choose_section(d)
        cost = _estimate_tokens(text)
        if used + cost > budget_tokens:
            overflow = True
            omitted.append(d.id)
            continue
        used += cost
        assembled.append({"id": d.id, "corpus": d.corpus_name, "title": d.title,
                          "section": heading, "body": text, "tokens": cost})
        for ref in d.related:
            if ref not in {r["ref"] for r in related}:
                related.append({"ref": ref})

    if not assembled:
        return {"mode": "id_list",
                "query": {"question": question, "path": path, "tags": tags},
                "budget_tokens": budget_tokens,
                "candidates": [d.id for d in candidates],
                "reason": "top candidate section exceeds budget; fetch individually"}

    return {"mode": "assembled",
            "query": {"question": question, "path": path, "tags": tags},
            "budget_tokens": budget_tokens, "used_tokens": used,
            "sections": assembled, "related": related,
            "omitted_for_budget": omitted}


def tool_resolve_term(corpus: Corpus, term: str,
                      subproject: str | None = None) -> dict[str, Any]:
    """docs.resolve_term — ground a term in the project's bounded context
    BEFORE acting on it. Returns term-map definition(s) + the docs that
    reference it (tags or body mention) + a verdict.

    verdict: resolved (>=1 definition) | ambiguous (>1, differing domains) |
    absent (no definition; reports any doc usages so the gap is visible)."""
    key = term.strip().lower()
    defs = [e for t, entries in corpus.terms.items() if t.lower() == key
            for e in entries]
    qtok = {key} | set(_tokenize(term))
    referencing: list[dict[str, Any]] = []
    for d in corpus.docs():
        if subproject is not None and d.subproject != subproject:
            continue
        tag_hit = any(key == t.lower() or key in t.lower() for t in d.tags)
        body = _load_body(d) or ""
        body_hit = key in body.lower() or bool(qtok & set(_tokenize(body)))
        if tag_hit or body_hit:
            referencing.append({**d.to_summary(),
                                "where": "tags" if tag_hit else "body"})
    referencing.sort(key=lambda r: (r["kind"], r["subproject"], r["id"]))
    if not defs:
        verdict = "absent"
    elif len({(e.get("domain") or "") for e in defs}) > 1:
        verdict = "ambiguous"
    else:
        verdict = "resolved"
    return {"term": term, "verdict": verdict,
            "definitions": [{"definition": e["definition"], "domain": e.get("domain"),
                             "corpus": e.get("corpus")} for e in defs],
            "referencing_docs": referencing}


# ---------------------------------------------------------------------------
# Federation (umbrella slice only) — per dec-20260604-memo-bank-topology-v2-
# federation-poc-claude-code-271d0fd0
#
# INVARIANT (dumb federation): this layer does fan-out + merge + schema-version
# check ONLY. No caching, no request shaping, no rate limiting, no query
# rewriting. Each of those is a separate future decision, not a quiet accretion.
#
# PoC seam: a "slice" is loaded here by reading its corpus from the local
# filesystem (one Corpus per slice). When slices become independently deployed
# MCP endpoints, swap `_load_slice` for an MCP client call — the merge logic,
# partial-status handling, and tool surface above this line do not change.
# ---------------------------------------------------------------------------

@dataclass
class SliceRef:
    """One entry in the island slice registry (.island-slices.json)."""
    name: str
    root: Path


@dataclass
class Federation:
    """The umbrella slice's view of every slice in the island."""
    slices: dict[str, Corpus] = field(default_factory=dict)   # name -> loaded
    unreachable: list[str] = field(default_factory=list)       # names that failed
    refs: list["SliceRef"] = field(default_factory=list)       # for reload
    signatures: dict[str, str] = field(default_factory=dict)   # name -> sig

    def reload_if_stale(self) -> list[str]:
        """Re-load any slice whose corpus content changed since last load
        (mtime-based). Returns the names reloaded. This is the PoC's update
        path — no restart needed — and is model/transport-agnostic."""
        reloaded: list[str] = []
        for ref in self.refs:
            if not ref.root.exists():
                if ref.name in self.slices:
                    del self.slices[ref.name]
                    if ref.name not in self.unreachable:
                        self.unreachable.append(ref.name)
                    reloaded.append(ref.name)
                continue
            sig = corpus_signature(ref.root)
            if sig != self.signatures.get(ref.name):
                try:
                    self.slices[ref.name] = _load_slice(ref)
                    self.signatures[ref.name] = sig
                    if ref.name in self.unreachable:
                        self.unreachable.remove(ref.name)
                    reloaded.append(ref.name)
                except Exception as exc:  # noqa: BLE001
                    LOG.warning("reload of slice '%s' failed: %s", ref.name, exc)
        return reloaded


def parse_registry(registry_path: Path) -> list[SliceRef]:
    """Read .island-slices.json; resolve slice roots relative to its location."""
    data = json.loads(registry_path.read_text())
    base = registry_path.resolve().parent
    refs: list[SliceRef] = []
    for entry in data.get("slices", []):
        name = entry["name"]
        root = (base / entry["root"]).resolve()
        refs.append(SliceRef(name=name, root=root))
    return refs


def _load_slice(ref: SliceRef) -> Corpus:
    """PoC: load a slice's corpus from the local filesystem.

    This is the single seam to swap for an MCP client call when slices become
    independently deployed endpoints. Everything downstream is transport-agnostic.
    """
    return load_corpus([(ref.name, ref.root)])


def load_federation(registry: list[SliceRef]) -> Federation:
    fed = Federation(refs=list(registry))
    for ref in registry:
        if not ref.root.exists():
            LOG.warning("slice '%s' unreachable: root missing %s",
                        ref.name, ref.root)
            fed.unreachable.append(ref.name)
            continue
        try:
            fed.slices[ref.name] = _load_slice(ref)
            fed.signatures[ref.name] = corpus_signature(ref.root)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("slice '%s' unreachable: %s", ref.name, exc)
            fed.unreachable.append(ref.name)
    return fed


def _slice_status(fed: Federation) -> list[dict[str, Any]]:
    ok = [{"slice": n, "status": "ok", "docs": len(c.by_id)}
          for n, c in sorted(fed.slices.items())]
    down = [{"slice": n, "status": "unreachable"}
            for n in sorted(fed.unreachable)]
    return ok + down


def _envelope(fed: Federation, results: Any) -> dict[str, Any]:
    """Wrap a federated result with slice_status + partial flag.

    `partial: true` whenever any slice is unreachable — the caller must never
    mistake an incomplete cross-corpus answer for a complete one.
    """
    return {
        "results": results,
        "slice_status": _slice_status(fed),
        "partial": bool(fed.unreachable),
    }


def fed_list(fed: Federation, subproject: str | None = None,
             kind: str | None = None, include_archive: bool = False
             ) -> dict[str, Any]:
    """Federated docs.list — concat across slices, dedupe by id, sort."""
    seen: dict[str, dict[str, Any]] = {}
    for corpus in fed.slices.values():
        for summary in tool_list(corpus, subproject, kind, include_archive):
            seen.setdefault(summary["id"], summary)
    merged = sorted(seen.values(),
                    key=lambda s: (s["kind"], s["subproject"], s["id"]))
    return _envelope(fed, merged)


def fed_get(fed: Federation, id: str) -> dict[str, Any]:
    """Federated docs.get — first non-error result across slices."""
    for corpus in fed.slices.values():
        r = tool_get(corpus, id)
        if "error" not in r:
            r["slice_status"] = _slice_status(fed)
            r["partial"] = bool(fed.unreachable)
            return r
    return {
        "error": f"id '{id}' not found in any slice",
        "slice_status": _slice_status(fed),
        "partial": bool(fed.unreachable),
    }


def fed_resolve_path(fed: Federation, path: str) -> dict[str, Any]:
    """Federated docs.resolve_path — gather matches across slices, re-rank
    globally by glob specificity (most specific first)."""
    matches: list[dict[str, Any]] = []
    for corpus in fed.slices.values():
        matches.extend(tool_resolve_path(corpus, path))
    matches.sort(key=lambda m: (
        -_specificity(m["matched_glob"])[0],
        -_specificity(m["matched_glob"])[1],
        -_specificity(m["matched_glob"])[2],
        m["subproject"], m["id"],
    ))
    return _envelope(fed, matches)


def fed_get_section(fed: Federation, id: str, section: str) -> dict[str, Any]:
    """Federated docs.get_section — first non-error across slices."""
    for corpus in fed.slices.values():
        r = tool_get_section(corpus, id, section)
        if "error" not in r:
            r["partial"] = bool(fed.unreachable)
            return r
    return {"error": f"id '{id}' not found in any slice",
            "slice_status": _slice_status(fed), "partial": bool(fed.unreachable)}


def fed_search_live(fed: Federation, query: str, subproject: str | None = None,
                    kind: str | None = None) -> dict[str, Any]:
    """Federated docs.search_live — merge ranked hits across slices, re-rank
    globally by score."""
    hits: list[dict[str, Any]] = []
    for corpus in fed.slices.values():
        hits.extend(tool_search_live(corpus, query, subproject, kind))
    hits.sort(key=lambda h: (-h["score"], h["subproject"], h["id"]))
    return _envelope(fed, hits)


def fed_search_archive(fed: Federation, query: str, feature: str | None = None,
                       subproject: str | None = None) -> dict[str, Any]:
    """Federated docs.search_archive — cold search merged across slices."""
    hits: list[dict[str, Any]] = []
    for corpus in fed.slices.values():
        hits.extend(tool_search_archive(corpus, query, feature, subproject))
    hits.sort(key=lambda h: (-h["score"], h["subproject"], h["id"]))
    return _envelope(fed, hits)


def fed_compose_context(fed: Federation, question: str | None = None,
                        path: str | None = None, tags: list[str] | None = None,
                        budget_tokens: int = 8000) -> dict[str, Any]:
    """Federated docs.compose_context — gather candidates across ALL slices,
    assemble under one global budget. Each section body comes from its owning
    slice (slice boundary respected); the budget is enforced globally so the
    bundle stays 'just enough' across the whole island."""
    # Build a transient merged view for candidate gathering + section fetch.
    # (Respects slices: every doc still carries its owning corpus_name.)
    merged = Corpus()
    for corpus in fed.slices.values():
        for did, d in corpus.by_id.items():
            merged.by_id.setdefault(did, d)
    result = tool_compose_context(merged, question=question, path=path,
                                  tags=tags, budget_tokens=budget_tokens)
    result["slice_status"] = _slice_status(fed)
    result["partial"] = bool(fed.unreachable)
    return result


def fed_resolve_term(fed: Federation, term: str,
                     subproject: str | None = None) -> dict[str, Any]:
    """Federated docs.resolve_term — union term-map definitions + referencing
    docs across slices."""
    defs: list[dict[str, Any]] = []
    seen_defs: set[tuple] = set()
    ref_by_id: dict[str, dict[str, Any]] = {}
    for corpus in fed.slices.values():
        r = tool_resolve_term(corpus, term, subproject)
        for d in r["definitions"]:
            sig = (d["definition"], d.get("domain"))
            if sig not in seen_defs:
                seen_defs.add(sig)
                defs.append(d)
        for rd in r["referencing_docs"]:
            ref_by_id.setdefault(rd["id"], rd)
    refs = sorted(ref_by_id.values(),
                  key=lambda r: (r["kind"], r["subproject"], r["id"]))
    verdict = ("absent" if not defs else
               "ambiguous" if len({(d.get("domain") or "") for d in defs}) > 1
               else "resolved")
    return {"term": term, "verdict": verdict, "definitions": defs,
            "referencing_docs": refs, "slice_status": _slice_status(fed),
            "partial": bool(fed.unreachable)}


FED_DISPATCH = {
    "docs.list": lambda fed, args: fed_list(fed, **args),
    "docs.get": lambda fed, args: fed_get(fed, **args),
    "docs.get_section": lambda fed, args: fed_get_section(fed, **args),
    "docs.search_live": lambda fed, args: fed_search_live(fed, **args),
    "docs.search_archive": lambda fed, args: fed_search_archive(fed, **args),
    "docs.resolve_path": lambda fed, args: fed_resolve_path(fed, **args),
    "docs.resolve_term": lambda fed, args: fed_resolve_term(fed, **args),
    "docs.compose_context": lambda fed, args: fed_compose_context(fed, **args),
}


# ---------------------------------------------------------------------------
# Stubs (returned with explicit "not implemented in v1" marker)
# ---------------------------------------------------------------------------

def stub(name: str, **received_args: Any) -> dict[str, Any]:
    return {
        "error": "not implemented in v1",
        "tool": name,
        "args_received": received_args,
        "tracked_under": "dec-20260517-the V2-nested docs decision"
                         "nested-docs-auth-98241800",
        "v1_implemented": sorted(TOOL_DISPATCH),
    }


# ---------------------------------------------------------------------------
# MCP tool schemas (JSON Schema for input validation by the MCP client)
# ---------------------------------------------------------------------------

TOOLS: list[Tool] = [
    Tool(
        name="docs.list",
        description="List documents in the corpus, with optional filters by "
                    "subproject (umbrella, server, frontend, game-app) and "
                    "kind (spec, state). Archive entries excluded by default.",
        inputSchema={
            "type": "object",
            "properties": {
                "subproject": {"type": "string"},
                "kind": {"type": "string"},
                "include_archive": {"type": "boolean", "default": False},
            },
        },
    ),
    Tool(
        name="docs.get",
        description="Get a full document by its stable id (e.g. "
                    "'schema-frontmatter-v1', 'auth-protocol'). Returns "
                    "frontmatter + body + path + corpus.",
        inputSchema={
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "string"}},
        },
    ),
    Tool(
        name="docs.get_section",
        description="Get a single H2 section of a doc by id + section heading "
                    "(rung 1 — load one slice, not the whole doc). On miss, "
                    "returns the available section headings to pick from.",
        inputSchema={
            "type": "object",
            "required": ["id", "section"],
            "properties": {
                "id": {"type": "string"},
                "section": {"type": "string"},
            },
        },
    ),
    Tool(
        name="docs.search_live",
        description="Content search over the HOT corpus (specs + state); "
                    "archive excluded by construction. Returns ranked pointers "
                    "(summary + score + snippet), not bodies. SCORING IS LEXICAL "
                    "(keyword overlap, no embeddings): for a natural-language or "
                    "topic query, FIRST expand it with domain synonyms and "
                    "related terms before calling — e.g. 'authentication' -> "
                    "'authentication auth login session HMAC signature OIDC JWT "
                    "token verification'. You (the calling model) do this in your "
                    "own loop; it sharply improves ranking. If unsure of the "
                    "corpus's vocabulary, call docs.list first and reuse its tags.",
        inputSchema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "subproject": {"type": "string"},
                "kind": {"type": "string"},
            },
        },
    ),
    Tool(
        name="docs.search_archive",
        description="Content search over the COLD archive only — a distinct "
                    "tool from search_live (no include_archive flag). Returns "
                    "ranked pointers, not bodies.",
        inputSchema={
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string"},
                "feature": {"type": "string"},
                "subproject": {"type": "string"},
            },
        },
    ),
    Tool(
        name="docs.resolve_path",
        description="Return docs whose applies_to globs match the given path, "
                    "ordered by glob specificity (most specific first). The "
                    "closest-wins precedence surface — use this when editing "
                    "a file to find applicable specs.",
        inputSchema={
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
        },
    ),
    Tool(
        name="docs.resolve_term",
        description="Ground a term in the project's bounded context: returns "
                    "term-map definition(s) + docs that reference it + a verdict "
                    "(resolved / ambiguous / absent). Use before acting on a "
                    "vague/umbrella term.",
        inputSchema={
            "type": "object",
            "required": ["term"],
            "properties": {
                "term": {"type": "string"},
                "subproject": {"type": "string"},
            },
        },
    ),
    Tool(
        name="docs.compose_context",
        description="Assemble a question-scoped context bundle under a token "
                    "budget (rung 4): one chosen section per candidate in "
                    "precedence order + first-degree related pointers. Degrades "
                    "to a precedence-ranked ID list if the top section won't fit "
                    "— never a mid-section truncation, never a whole-corpus dump. "
                    "Candidate matching is LEXICAL: phrase `question` with the "
                    "domain's actual terms (expand synonyms — auth/HMAC/OIDC, "
                    "not just 'authentication') so the right doc is gathered.",
        inputSchema={
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "path": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "budget_tokens": {"type": "integer", "default": 8000},
            },
        },
    ),
]

TOOL_DISPATCH = {
    "docs.list": lambda corpus, args: tool_list(corpus, **args),
    "docs.get": lambda corpus, args: tool_get(corpus, **args),
    "docs.get_section": lambda corpus, args: tool_get_section(corpus, **args),
    "docs.search_live": lambda corpus, args: tool_search_live(corpus, **args),
    "docs.search_archive": lambda corpus, args: tool_search_archive(corpus, **args),
    "docs.resolve_path": lambda corpus, args: tool_resolve_path(corpus, **args),
    "docs.resolve_term": lambda corpus, args: tool_resolve_term(corpus, **args),
    "docs.compose_context": lambda corpus, args: tool_compose_context(corpus, **args),
}


# ---------------------------------------------------------------------------
# MCP wiring
# ---------------------------------------------------------------------------

def build_server(ctx: Any, dispatch: dict | None = None) -> Server:
    """Build the MCP server over `ctx` (a Corpus for slice mode, a Federation
    for federation mode), routing implemented tools through `dispatch`.

    Defaults to TOOL_DISPATCH (slice mode) so existing callers keep working;
    federation mode passes FED_DISPATCH.
    """
    if dispatch is None:
        dispatch = TOOL_DISPATCH
    server = Server("memo-bank")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        args = arguments or {}
        # Lazy reload: pick up corpus changes without a restart (federation).
        reload_fn = getattr(ctx, "reload_if_stale", None)
        if callable(reload_fn):
            try:
                changed = reload_fn()
                if changed:
                    LOG.info("reloaded stale slices before %s: %s", name, changed)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("reload check failed: %s", exc)
        impl = dispatch.get(name)
        try:
            if impl is not None:
                result = impl(ctx, args)
            else:
                result = stub(name, **args)
        except TypeError as exc:
            result = {"error": f"invalid args for {name}: {exc}",
                      "args_received": args}
        except Exception as exc:  # noqa: BLE001
            result = {"error": f"{type(exc).__name__}: {exc}"}
        return [TextContent(type="text",
                            text=json.dumps(result, indent=2, default=str))]

    return server


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_corpus_arg(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError(
            f"--corpus must be NAME=PATH, got: {value!r}")
    name, _, path = value.partition("=")
    if not name or not path:
        raise argparse.ArgumentTypeError(
            f"--corpus must be NAME=PATH with both parts, got: {value!r}")
    return (name.strip(), Path(path.strip()))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus", action="append", type=parse_corpus_arg, default=[],
        metavar="NAME=PATH",
        help="Slice mode: serve a single corpus. Example: "
             "--corpus server=/repos/my-service")
    parser.add_argument(
        "--federation", metavar="REGISTRY.json", type=Path, default=None,
        help="Federation mode (umbrella slice): fan out across the slices "
             "listed in the .island-slices.json registry and merge results.")
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument(
        "--list-tools", action="store_true",
        help="Print the registered tool surface and exit (smoke test).")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    if args.federation and args.corpus:
        LOG.error("pass either --federation OR --corpus, not both")
        return 2

    if args.federation:
        registry = parse_registry(args.federation)
        fed = load_federation(registry)
        LOG.info("memo-bank federation ready: %d slices ok, %d unreachable",
                 len(fed.slices), len(fed.unreachable))
        if args.list_tools:
            for t in TOOLS:
                marker = "" if t.name in FED_DISPATCH else " [STUB]"
                print(f"{t.name}{marker}")
            return 0
        asyncio.run(_run_stdio(fed, FED_DISPATCH))
        return 0

    if not args.corpus:
        LOG.error("no corpora configured; pass --corpus NAME=PATH "
                  "or --federation REGISTRY.json")
        return 2

    corpus = load_corpus(args.corpus)
    LOG.info("memo-bank slice ready: %d docs across %d corpora",
             len(corpus.by_id), len(corpus.corpora))

    if args.list_tools:
        for t in TOOLS:
            stub_marker = " [STUB]" if t.name not in TOOL_DISPATCH else ""
            print(f"{t.name}{stub_marker}")
        return 0

    asyncio.run(_run_stdio(corpus, TOOL_DISPATCH))
    return 0


async def _run_stdio(ctx: Any, dispatch: dict) -> None:
    server = build_server(ctx, dispatch)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    sys.exit(main())
