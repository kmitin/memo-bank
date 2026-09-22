"""Opt-in semantic re-ranking (`memobank serve --rerank jev`). All offline: the
scorer and the HTTP layer are replaced, so these run without a key or network."""
from __future__ import annotations

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import memo_bank as mb  # noqa: E402
import rerank  # noqa: E402
from test_memo_bank import _build_island  # noqa: E402

KEY = "ts-secret-key-do-not-leak"


@pytest.fixture(scope="module")
def island(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("island")
    _build_island(root)
    return root


def _fed(island: Path) -> mb.Federation:
    return mb.load_federation(mb.parse_registry(island / ".island-slices.json"))


class Recorder:
    """A fake scorer: fixed relevance per doc title, records every card sent."""

    def __init__(self, by_title: dict[str, float], fail: bool = False):
        self.by_title, self.fail, self.cards = by_title, fail, []

    def __call__(self, query: str, card: dict) -> float:
        self.cards.append(card)
        if self.fail:
            raise rerank.RerankError("TypeSafe HTTP 503")
        return self.by_title.get(card["title"], 0.01)


def _cand(i: str, score: float = 1.0):
    return ({"id": i, "score": score}, {"title": i, "tags": [], "text": ""})


# ---------- Reranker ----------

def test_rerank_sorts_by_relevance_and_reports_verdict():
    r = rerank.Reranker(score_fn=Recorder({"b": 0.9, "a": 0.2}))
    hits, meta = r.rerank("q", [_cand("a"), _cand("b"), _cand("c")])
    assert [h["id"] for h in hits] == ["b", "a", "c"]
    assert hits[0]["relevance"] == 0.9
    assert meta["status"] == "ok" and meta["governing_doc_found"] is True
    assert meta["candidates_scored"] == 3


def test_rerank_below_threshold_means_no_governing_doc():
    r = rerank.Reranker(score_fn=Recorder({"a": 0.3}), threshold=0.5)
    _, meta = r.rerank("q", [_cand("a"), _cand("b")])
    assert meta["governing_doc_found"] is False and meta["top_relevance"] == 0.3


def test_rerank_failure_falls_back_to_lexical_order():
    r = rerank.Reranker(score_fn=Recorder({}, fail=True))
    hits, meta = r.rerank("q", [_cand("a"), _cand("b")])
    assert [h["id"] for h in hits] == ["a", "b"]
    assert all("relevance" not in h for h in hits)
    assert meta["status"] == "failed" and "503" in meta["reason"]
    assert meta["governing_doc_found"] is None  # unknown, not "no doc"


def test_rerank_caches_repeated_pairs():
    rec = Recorder({"a": 0.9})
    r = rerank.Reranker(score_fn=rec)
    r.rerank("q", [_cand("a")])
    r.rerank("q", [_cand("a")])
    assert len(rec.cards) == 1


def test_rerank_cache_is_bounded():
    rec = Recorder({})
    r = rerank.Reranker(score_fn=rec, cache_size=2)
    for i in range(5):
        r.rerank("q", [_cand(str(i))])
    assert len(r._cache) == 2


def test_rerank_skips_empty_query_without_calling_out():
    rec = Recorder({})
    _, meta = rerank.Reranker(score_fn=rec).rerank("  ", [_cand("a")])
    assert meta["status"] == "skipped" and rec.cards == []


# ---------- search_live_reranked ----------

def test_reranked_search_lets_a_zero_overlap_doc_win(island):
    # "broad-src" shares no word with the query; lexical alone cannot return it.
    q = "widget foo"
    assert "broad-src" not in [h["id"] for h in mb.fed_search_live(_fed(island), q)["results"]]
    rec = Recorder({"Broad Src": 0.95, "Widget Rules": 0.4})
    out = mb.search_live_reranked(_fed(island), rerank.Reranker(score_fn=rec), q)
    assert out["results"][0]["id"] == "broad-src"
    assert out["rerank"]["governing_doc_found"] is True
    assert "slice_status" in out and out["partial"] is False


def test_reranked_search_never_sends_archive_text(island):
    rec = Recorder({})
    mb.search_live_reranked(_fed(island), rerank.Reranker(score_fn=rec), "widget legacy")
    assert rec.cards and all("Old Widget" != c["title"] for c in rec.cards)
    assert all("legacy" not in c["text"] for c in rec.cards)


def test_reranked_search_respects_shortlist(island):
    rec = Recorder({})
    mb.search_live_reranked(_fed(island), rerank.Reranker(score_fn=rec, shortlist=1), "widget")
    assert len(rec.cards) == 1


def test_reranked_search_fallback_matches_plain_lexical(island):
    fed = _fed(island)
    out = mb.search_live_reranked(fed, rerank.Reranker(score_fn=Recorder({}, fail=True)), "widget")
    plain = mb.fed_search_live(fed, "widget")["results"]
    assert [h["id"] for h in out["results"]] == [h["id"] for h in plain]
    assert out["rerank"]["status"] == "failed"


def test_reranked_search_in_slice_mode(island):
    corpus = mb.load_corpus([("main", island)])
    out = mb.search_live_reranked(corpus, rerank.Reranker(score_fn=Recorder({"Widget Rules": 0.8})),
                                  "widget")
    assert out["results"][0]["id"] == "widget-rules" and "slice_status" not in out


def test_with_reranked_search_only_swaps_search_live():
    rr = rerank.Reranker(score_fn=Recorder({}))
    tools, dispatch = mb.with_reranked_search(mb.TOOLS, mb.FED_DISPATCH, rr)
    by_name = {t.name: t for t in tools}
    assert "SEMANTIC" in by_name["docs.search_live"].description
    assert "LEXICAL" in by_name["docs.compose_context"].description
    assert dispatch["docs.search_live"] is not mb.FED_DISPATCH["docs.search_live"]
    assert dispatch["docs.get"] is mb.FED_DISPATCH["docs.get"]
    assert {t.name for t in tools} == {t.name for t in mb.TOOLS}


# ---------- JevClient ----------

class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _answer(noul) -> bytes:
    return json.dumps({"answers": {"governs": {"type": "noul", "noul": noul}}}).encode()


def test_client_sends_bearer_key_and_parses_noul(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout):
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = json.loads(req.data)
        return FakeResp(_answer(0.87))
    monkeypatch.setattr(rerank, "_open", fake_urlopen)
    p = rerank.JevClient(api_key=KEY).governs("q", {"title": "t", "tags": [], "text": ""})
    assert p == 0.87
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["body"]["questions"]["governs"] == rerank.GOVERNS


def test_client_retries_rate_limit_then_succeeds(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "slow down", {}, None)
        return FakeResp(_answer(0.5))
    monkeypatch.setattr(rerank, "_open", fake_urlopen)
    monkeypatch.setattr(rerank.time, "sleep", lambda s: None)
    assert rerank.JevClient(api_key=KEY).governs("q", {}) == 0.5 and len(calls) == 2


def test_client_errors_never_contain_the_key(monkeypatch):
    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "bad",
                                     {}, io.BytesIO(f"invalid key {KEY}".encode()))
    monkeypatch.setattr(rerank, "_open", fake_urlopen)
    client = rerank.JevClient(api_key=KEY)
    with pytest.raises(rerank.RerankError) as exc:
        client.governs("q", {})
    assert KEY not in str(exc.value) and "401" in str(exc.value)
    assert KEY not in repr(client)


@pytest.mark.parametrize("payload", [_answer(1.7), _answer("x"), b'{"answers": {}}', b"not json"])
def test_client_rejects_malformed_answers(monkeypatch, payload):
    monkeypatch.setattr(rerank, "_open", lambda req, timeout: FakeResp(payload))
    with pytest.raises(rerank.RerankError):
        rerank.JevClient(api_key=KEY).governs("q", {})


def test_client_rejects_oversized_response(monkeypatch):
    big = b" " * (rerank.MAX_RESPONSE_BYTES + 10)
    monkeypatch.setattr(rerank, "_open", lambda req, timeout: FakeResp(big))
    with pytest.raises(rerank.RerankError, match="too large"):
        rerank.JevClient(api_key=KEY).governs("q", {})


def test_client_does_not_follow_redirects():
    handler = rerank._NoRedirect()
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://elsewhere") is None


def test_from_env_rejects_malformed_key(monkeypatch):
    monkeypatch.setenv(rerank.API_KEY_ENV, "abc\ndef")
    with pytest.raises(rerank.RerankError) as exc:
        rerank.from_env()
    assert "abc" not in str(exc.value)


def test_client_requires_https():
    with pytest.raises(ValueError):
        rerank.JevClient(api_key=KEY, endpoint="http://api.typesafe.ai/v1/systemone")


# ---------- configuration ----------

def test_from_env_requires_the_key(monkeypatch):
    monkeypatch.delenv(rerank.API_KEY_ENV, raising=False)
    with pytest.raises(rerank.RerankError, match=rerank.API_KEY_ENV):
        rerank.from_env()


@pytest.mark.parametrize("kw", [{"shortlist": 0}, {"threshold": 1.5}])
def test_from_env_validates_settings(monkeypatch, kw):
    monkeypatch.setenv(rerank.API_KEY_ENV, KEY)
    with pytest.raises(ValueError):
        rerank.from_env(**kw)


def test_serve_rerank_without_key_exits_2(monkeypatch, island):
    monkeypatch.delenv(rerank.API_KEY_ENV, raising=False)
    monkeypatch.setattr(sys, "argv", ["memobank serve", "--federation",
                                      str(island / ".island-slices.json"),
                                      "--rerank", "jev", "--list-tools"])
    assert mb.main() == 2


def test_serve_rerank_lists_the_full_surface(monkeypatch, island, capsys):
    monkeypatch.setenv(rerank.API_KEY_ENV, KEY)
    monkeypatch.setattr(sys, "argv", ["memobank serve", "--federation",
                                      str(island / ".island-slices.json"),
                                      "--rerank", "jev", "--list-tools"])
    assert mb.main() == 0
    out = capsys.readouterr().out
    assert "docs.search_live" in out and "[STUB]" not in out
