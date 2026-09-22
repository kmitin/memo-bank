#!/usr/bin/env python3
"""Retrieval arm: does Jev find the governing doc better than lexical search?

Runs every part listed under `retrieval:` in DATA/jev-ab.yaml. Each part names a
memo-bank registry (the corpus), a labelled query file, and optionally a frozen
query-expansion module (a baseline). Query files hold `queries:` entries with
`q`, `expect` (doc id or list; empty = no doc governs, the right answer is to
abstain) and `hard` (or `semantic_hard`) for vocabulary-mismatch queries.

Arms
  lexical        memo-bank's own docs.search_live ranking
  expansion      the same, on the frozen synonym-expanded query (the part's
                 `expansion` module, if given — the "cheap fix" baseline)
  jev            one Noul per (query, doc) pair over every hot doc, sorted by noul
                 — the TypeSafe rerank cookbook pattern, with no shortlist since
                 the corpus is small
  lexical+jev@5  lexical order, with its top 5 re-sorted by the jev noul
  shipped        mb.search_live_reranked — the `serve --rerank jev` code path
                 (shortlist 20, padded with the rest of the hot corpus)

Abstention (parts with unanswerable queries): does the top score separate "a doc governs this" from
"nothing does"? Reported as AUROC of the top score, plus Jev at noul 0.5.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
import memo_bank as mb  # noqa: E402
from project_config import load as load_project_config  # noqa: E402
import rerank  # noqa: E402
from jev_client import Jev  # noqa: E402


# The shipped question, card and search path: the eval measures exactly what
# `memobank serve --rerank jev` runs.
GOVERNS = rerank.GOVERNS


def hot_docs(fed) -> dict[str, dict]:
    out = {}
    for corpus in fed.slices.values():
        for d in corpus.by_id.values():
            if d.indexed and d.kind in ("spec", "state"):
                out[d.id] = rerank.doc_card(d.title, d.tags, mb._load_body(d) or "")
    return out


def lexical_ids(fed, q: str) -> list[str]:
    return [r["id"] for r in mb.fed_search_live(fed, q)["results"]]


def lexical_top_score(fed, q: str) -> float:
    r = mb.fed_search_live(fed, q)["results"]
    return r[0]["score"] if r else 0.0


def rank_of(ids: list[str], expect: set[str]) -> int | None:
    for i, did in enumerate(ids, 1):
        if did in expect:
            return i
    return None


def metrics(ranks: list[int | None]) -> dict:
    n = len(ranks)
    if not n:
        return {"n": 0}
    return {"n": n,
            "hit@1": round(sum(r == 1 for r in ranks) / n, 3),
            "hit@3": round(sum(r is not None and r <= 3 for r in ranks) / n, 3),
            "mrr": round(sum(1 / r for r in ranks if r) / n, 3)}


def auroc(pos: list[float], neg: list[float]) -> float:
    """P(score of a random answerable query > score of a random unanswerable one)."""
    if not pos or not neg:
        return float("nan")
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return round(wins / (len(pos) * len(neg)), 3)


per_call: list[float] = []  # recorded latency of every pair, across both parts


def score_all(jev: Jev, queries: list[str], docs: dict[str, dict]) -> tuple[dict, list]:
    """noul[q][doc_id] for every pair, plus per-query latency (the slowest of its parallel pairs)."""
    pairs = [(q, did) for q in queries for did in docs]

    def one(p):
        q, did = p
        st, qs = {"query": q, "document": docs[did]}, {"governs": GOVERNS}
        a = jev.ask(st, qs)
        return a["governs"]["noul"], jev.recorded_ms(st, qs) or 0.0

    nouls: dict[str, dict[str, float]] = {q: {} for q in queries}
    wall: list[float] = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        for q in queries:
            vals = list(pool.map(one, [(q, did) for did in docs]))
            # a query's pairs run in parallel, so its latency is its slowest call
            # (taken from the recorded live latency, so cached reruns report it too)
            wall.append(max(ms for _, ms in vals))
            per_call.extend(ms for _, ms in vals)
            for did, (v, _) in zip(docs, vals):
                nouls[q][did] = v
    assert len(pairs) == sum(len(v) for v in nouls.values())
    return nouls, wall


def run_part(name: str, fed, cases: list[dict], jev: Jev, expand=None) -> dict:
    docs = hot_docs(fed)
    qs = [c["q"] for c in cases]
    nouls, wall = score_all(jev, qs, docs)

    def jev_ids(q):
        return sorted(docs, key=lambda d: (-nouls[q][d], d))

    def rerank5(q):
        lex = lexical_ids(fed, q)
        head = sorted(lex[:5], key=lambda d: (-nouls[q].get(d, 0), lex.index(d)))
        return head + lex[5:]

    shipped = rerank.Reranker(score_fn=lambda q, card: jev.ask(
        {"query": q, "document": card}, {"governs": GOVERNS})["governs"]["noul"])
    shipped_out = {q: mb.search_live_reranked(fed, shipped, q) for q in qs}

    arms = {"lexical": lambda q: lexical_ids(fed, q),
            "lexical+jev@5": rerank5,
            "jev": jev_ids,
            "shipped": lambda q: [h["id"] for h in shipped_out[q]["results"]]}
    if expand:
        arms["expansion"] = lambda q: lexical_ids(fed, expand(q))

    answerable = [c for c in cases if c["expect"]]
    rows = []
    for c in cases:
        row = {"q": c["q"], "expect": sorted(c["expect"]), "hard": c["hard"],
               "lexical_top_score": lexical_top_score(fed, c["q"]),
               "jev_max_noul": round(max(nouls[c["q"]].values()), 4),
               "jev_top": jev_ids(c["q"])[:3]}
        for arm, f in arms.items():
            row[f"rank_{arm}"] = rank_of(f(c["q"]), set(c["expect"])) if c["expect"] else None
        rows.append(row)

    summary: dict = {"docs": len(docs), "queries": len(cases), "arms": {}}
    for arm in arms:
        summary["arms"][arm] = {
            sub: metrics([r[f"rank_{arm}"] for r in rows if r["expect"] and pred(r)])
            for sub, pred in (("all", lambda r: True),
                              ("hard", lambda r: r["hard"]),
                              ("easy", lambda r: not r["hard"]))}

    unans = [r for r in rows if not r["expect"]]
    if unans:
        ans = [r for r in rows if r["expect"]]
        summary["abstention"] = {
            "n_answerable": len(ans), "n_unanswerable": len(unans),
            "auroc_lexical_top_score": auroc([r["lexical_top_score"] for r in ans],
                                             [r["lexical_top_score"] for r in unans]),
            "auroc_jev_max_noul": auroc([r["jev_max_noul"] for r in ans],
                                        [r["jev_max_noul"] for r in unans]),
            "jev@0.5_correctly_abstained": f"{sum(r['jev_max_noul'] < 0.5 for r in unans)}/{len(unans)}",
            "jev@0.5_wrongly_abstained": f"{sum(r['jev_max_noul'] < 0.5 for r in ans)}/{len(ans)}",
            "shipped_governing_doc_found_on_unanswerable":
                f"{sum(shipped_out[r['q']]['rerank']['governing_doc_found'] for r in unans)}/{len(unans)}",
            "shipped_governing_doc_found_on_answerable":
                f"{sum(shipped_out[r['q']]['rerank']['governing_doc_found'] for r in ans)}/{len(ans)}",
        }
    summary["latency_ms_per_query_wall"] = {
        "median": round(statistics.median(wall), 1), "max": round(max(wall), 1)}
    return {"part": name, "summary": summary, "rows": rows,
            "_answerable": len(answerable)}


def load_config(data: Path) -> dict:
    """DATA/jev-ab.yaml, with every path resolved relative to it."""
    cfg = yaml.safe_load((data / "jev-ab.yaml").read_text())
    res = lambda p: (data / p).resolve() if p else None
    for part in cfg.get("retrieval", []):
        for k in ("registry", "queries", "expansion"):
            part[k] = res(part.get(k))
    if cfg.get("drift"):
        for k in ("registry", "cases"):
            cfg["drift"][k] = res(cfg["drift"][k])
    return cfg


def load_cases(path: Path) -> list[dict]:
    return [{"q": c["q"], "hard": bool(c.get("hard", c.get("semantic_hard"))),
             "expect": c["expect"] if isinstance(c["expect"], list) else [c["expect"]]}
            for c in yaml.safe_load(path.read_text())["queries"]]


def load_expand(path: Path | None):
    if path is None:
        return None
    spec = importlib.util.spec_from_file_location("jev_ab_expansion", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.expand


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, required=True,
                    help="directory holding jev-ab.yaml; cache/ and results/ are written there")
    a = ap.parse_args()
    data = a.data.expanduser().resolve()
    cfg = load_config(data)

    (data / "cache").mkdir(exist_ok=True)
    (data / "results").mkdir(exist_ok=True)
    jev = Jev(data / "cache" / "retrieval.json")
    parts = []
    try:
        for part in cfg["retrieval"]:
            fed = mb.load_federation(load_project_config(part["registry"]).slices)
            parts.append(run_part(part["name"], fed, load_cases(part["queries"]),
                                  jev, load_expand(part["expansion"])))
    finally:
        jev.save()

    result = {"model": jev.served_model or jev.model,
              "live_calls": jev.live_calls, "input_tokens": jev.input_tokens,
              "cost_usd": round(jev.cost_usd(), 5),
              "call_latency_ms": {"median": round(statistics.median(per_call), 1),
                                  "p90": round(sorted(per_call)[int(.9 * len(per_call))], 1)},
              "parts": parts}
    (data / "results" / "retrieval.json").write_text(json.dumps(result, indent=1))
    for p in result["parts"]:
        print(f"\n== {p['part']}  ({p['summary']['docs']} docs, {p['summary']['queries']} queries)")
        print(f"{'arm':15} {'hard@1':>7} {'easy@1':>7} {'all@1':>7} {'all@3':>7} {'mrr':>6}")
        for arm, m in p["summary"]["arms"].items():
            print(f"{arm:15} {m['hard'].get('hit@1', '-'):>7} {m['easy'].get('hit@1', '-'):>7} "
                  f"{m['all']['hit@1']:>7} {m['all']['hit@3']:>7} {m['all']['mrr']:>6}")
        if "abstention" in p["summary"]:
            print("abstention:", json.dumps(p["summary"]["abstention"]))
        print("latency per query (slowest of its parallel pairs, ms):", p["summary"]["latency_ms_per_query_wall"])
    print(f"\nmodel {result['model']} · {result['live_calls']} live calls · "
          f"{result['input_tokens']} input tokens · ${result['cost_usd']} · "
          f"per-call {result['call_latency_ms']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
