"""Opt-in semantic re-ranking for docs.search_live, backed by TypeSafe's Jev.

Lexical search is fast and offline but misses when a query's words differ from
the doc's, and it always returns *some* top hit. This re-ranker asks Jev one
yes/no question per (query, candidate doc) pair — "is this the doc to read?" —
and sorts by the probability. The top probability doubles as a verdict: when no
candidate crosses the threshold, no hot doc governs the topic.

Measured in eval/jev-ab (a real project corpus): hard-query hit@1 0.20 -> 0.80 on the
pre-existing gold set, and a clean "no governing doc" signal (AUROC 1.00).

Deliberately:
  * off unless `memobank serve --rerank jev` — the default server stays offline
    and sends nothing anywhere;
  * stdlib only — no new dependency for users who never enable it;
  * fail-open — any API failure returns the plain lexical ranking with the reason
    attached, so search never breaks because a remote service did;
  * only HOT doc text (title, tags, the first BODY_CHARS of the body) and the
    query are sent; archive entries never are.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

LOG = logging.getLogger("memo-bank.rerank")

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
API_KEY_ENV = "TYPESAFE_API_KEY"
DEFAULT_MODEL = "jev-latest"
BODY_CHARS = 4000        # ~1k tokens of doc text per pair
MAX_RESPONSE_BYTES = 1 << 20
RETRYABLE = (429, 529)   # rate limited / overloaded, per the TypeSafe API docs

# The one judgment, asked per (query, doc) pair. Generic on purpose: nothing in
# it names a project, a doc or a query. eval/jev-ab measures exactly this text,
# so changing it means re-running that eval.
GOVERNS: dict[str, Any] = {
    "type": "noul",
    "instructions": (
        "A developer working in this codebase asked `query`. Is `document` the one "
        "they should read to answer it — does it state the rules, contract, or facts "
        "that directly address the topic of `query`?"),
    "criteria": {
        "true": "The document's own subject is what the query asks about; its content "
                "answers the question or states the rules that govern it.",
        "false": "The document is about a different subject, or only mentions the "
                 "query's topic in passing (for example an index or overview that "
                 "points elsewhere), or the topic is not covered at all.",
    },
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib would re-send the Authorization header
    to wherever it points. A 3xx surfaces as an HTTPError instead."""

    def redirect_request(self, *args, **kwargs):
        return None


_open = urllib.request.build_opener(_NoRedirect).open


class RerankError(RuntimeError):
    """A re-ranking call failed. Messages never contain the API key."""


def doc_card(title: str, tags: list[str], body: str) -> dict[str, Any]:
    """What Jev sees of a doc. Kept identical to the eval's card."""
    return {"title": title, "tags": list(tags), "text": body[:BODY_CHARS]}


@dataclass
class JevClient:
    """Minimal client for TypeSafe's System One endpoint (stdlib HTTP)."""
    api_key: str
    model: str = DEFAULT_MODEL
    timeout: float = 8.0     # search is interactive: bound the worst case (~17 s)
    attempts: int = 2
    endpoint: str = ENDPOINT

    def __post_init__(self) -> None:
        if not self.endpoint.startswith("https://"):
            raise ValueError("TypeSafe endpoint must be https")

    def __repr__(self) -> str:  # keep the key out of logs and tracebacks
        return f"JevClient(model={self.model!r}, endpoint={self.endpoint!r})"

    def ask(self, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
        """POST one request; return the parsed response body."""
        body = json.dumps({"model": self.model, "state": state,
                           "questions": questions}).encode()
        for attempt in range(self.attempts):
            req = urllib.request.Request(self.endpoint, data=body, method="POST", headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "User-Agent": "memo-bank-rerank"})
            try:
                with _open(req, timeout=self.timeout) as resp:
                    raw = resp.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise RerankError("TypeSafe response too large")
                return json.loads(raw)
            except urllib.error.HTTPError as exc:
                if exc.code in RETRYABLE and attempt + 1 < self.attempts:
                    time.sleep(0.5 * 2 ** attempt)
                    continue
                # status only: the body could echo request details
                raise RerankError(f"TypeSafe HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt + 1 < self.attempts:
                    time.sleep(0.5 * 2 ** attempt)
                    continue
                raise RerankError(f"TypeSafe unreachable: {type(exc).__name__}") from None
            except json.JSONDecodeError:
                raise RerankError("TypeSafe returned invalid JSON") from None
        raise RerankError("TypeSafe request failed")  # pragma: no cover

    def governs(self, query: str, card: dict[str, Any]) -> float:
        """P(card is the doc to read for query), validated to [0, 1]."""
        resp = self.ask({"query": query, "document": card}, {"governs": GOVERNS})
        try:
            p = float(resp["answers"]["governs"]["noul"])
        except (KeyError, TypeError, ValueError):
            raise RerankError("TypeSafe response missing the noul answer") from None
        if not 0.0 <= p <= 1.0:
            raise RerankError("TypeSafe noul out of range")
        return p


@dataclass
class Reranker:
    """Re-sorts a lexical candidate list by Jev's relevance probability.

    `score_fn(query, card) -> float` is injectable so tests run offline.
    """
    score_fn: Callable[[str, dict[str, Any]], float]
    shortlist: int = 20      # candidates scored per query (one call each)
    threshold: float = 0.5   # top relevance below this -> no governing doc
    workers: int = 16      # a 16-doc corpus scores in one round (~0.8 s)
    cache_size: int = 2048
    provider: str = "jev"
    _cache: OrderedDict = field(default_factory=OrderedDict, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _cached_score(self, query: str, card: dict[str, Any]) -> float:
        """LRU-cached score: an agent often repeats a search, and a repeat costs
        nothing. Scoring runs outside the lock so calls stay parallel."""
        key = hashlib.sha256(json.dumps([query, card], sort_keys=True).encode()).hexdigest()
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        p = self.score_fn(query, card)
        with self._lock:
            self._cache[key] = p
            if len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)
        return p

    def rerank(self, query: str, candidates: list[tuple[dict[str, Any], dict[str, Any]]],
               limit: int = 10) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """candidates: [(hit_summary, card)] in lexical order, already cut to the
        shortlist. Returns (hits sorted by relevance, rerank metadata). On any
        failure returns the lexical order and says why."""
        # governing_doc_found stays None ("unknown") unless scoring succeeded
        meta: dict[str, Any] = {"provider": self.provider, "threshold": self.threshold,
                                "candidates_scored": 0, "governing_doc_found": None}
        if not query.strip() or not candidates:
            meta.update(status="skipped")
            return [h for h, _ in candidates][:limit], meta
        try:
            with ThreadPoolExecutor(max_workers=min(self.workers, len(candidates))) as pool:
                probs = list(pool.map(lambda c: self._cached_score(query, c[1]), candidates))
        except RerankError as exc:
            LOG.warning("rerank failed, falling back to lexical order: %s", exc)
            meta.update(status="failed", reason=str(exc))
            return [h for h, _ in candidates][:limit], meta
        order = sorted(range(len(candidates)), key=lambda i: (-probs[i], i))
        hits = [{**candidates[i][0], "relevance": round(probs[i], 4)} for i in order]
        top = probs[order[0]]
        meta.update(status="ok", candidates_scored=len(candidates),
                    top_relevance=round(top, 4), governing_doc_found=top >= self.threshold)
        return hits[:limit], meta


def from_env(shortlist: int = 20, threshold: float = 0.5,
             model: str = DEFAULT_MODEL) -> Reranker:
    """Build the Jev re-ranker from TYPESAFE_API_KEY. Raises if it is unset."""
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise RerankError(f"--rerank jev needs the {API_KEY_ENV} environment variable")
    if not key.isprintable() or " " in key:
        # http.client would reject it with an error that echoes the header value
        raise RerankError(f"{API_KEY_ENV} contains whitespace or control characters")
    if shortlist < 1:
        raise ValueError("shortlist must be >= 1")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be within [0, 1]")
    client = JevClient(api_key=key, model=model)
    return Reranker(score_fn=client.governs, shortlist=shortlist, threshold=threshold)
