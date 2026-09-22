# Does Jev improve memo-bank? An A/B

[Jev](https://docs.typesafe.ai) (TypeSafe, early access Sept 2026) is a
non-generative model: state and typed questions in, probabilities out. We tested
it in the two places memo-bank relies on a judgment that code cannot make:
**finding the governing doc for a topic** and **deciding whether a drift finding
matters**.

**Short answer: yes for retrieval, including a capability memo-bank lacks today
(saying "no doc governs this"). That result shipped as `memobank serve --rerank
jev`. For drift, the test set was too easy: Jev and Sonnet 5 both scored
perfectly, so the result is "it can", not "it is good enough to trust".**

The corpus is a real, private project's memo-bank: a small app with backend,
web and mobile slices. Its docs, queries and diffs describe that product, so they
stay private. This directory publishes the harness and the aggregate numbers
only. Query and case labels were written and committed before any Jev call.

## Retrieval: finding the governing doc

One Noul per (query, doc) pair — "is `document` the one they should read to
answer `query`?" — sorted by probability. This is TypeSafe's
[rerank cookbook](https://docs.typesafe.ai/cookbooks/rerank_typesafe.md) pattern.
The question and doc card are imported from [`rerank.py`](../../rerank.py), so the
eval measures exactly what ships.

### Part A: an existing gold set

A 17-doc sandbox and 9 hand-labeled queries, 5 of them vocabulary-mismatch
("hard"). These labels were written months earlier for an embedder experiment,
which also recorded the lexical, expansion and embedding baselines:

| scorer | hard hit@1 (5) | easy hit@1 (4) | all hit@1 | MRR |
|---|---|---|---|---|
| lexical (memo-bank today) | 0.20 | 1.00 | 0.56 | 0.71 |
| query expansion (frozen thesaurus) | 0.60 | 1.00 | 0.78 | 0.89 |
| MiniLM embeddings (earlier experiment) | 0.60 | 0.75 | 0.67 | – |
| **Jev, every doc** | **0.80** | **1.00** | **0.89** | **0.94** |
| **lexical top 5, re-sorted by Jev** | **1.00** | **1.00** | **1.00** | **1.00** |
| **shipped path (`--rerank jev`)** | **0.80** | **1.00** | **0.89** | **0.94** |

Jev is the first scorer to beat query expansion on the hard subset, and it
doesn't lose the easy queries the way embeddings did. Its one miss ranked the
right doc second, behind an ops README that also covers the topic.

### Part B: live corpus, 40 pre-registered queries

The live corpus (16 docs). There are 32 answerable queries (20 hard, 12 easy)
and 8 about topics no doc covers; absence was checked by grep.

| scorer | hard hit@1 | easy hit@1 | all hit@1 | MRR |
|---|---|---|---|---|
| lexical | 0.60 | 1.00 | 0.75 | 0.82 |
| query expansion | 0.45 | 0.83 | 0.59 | 0.73 |
| lexical top 5, re-sorted by Jev | 0.90 | 1.00 | 0.94 | 0.95 |
| **Jev, every doc** | **1.00** | **1.00** | **1.00** | **1.00** |
| **shipped path (`--rerank jev`)** | **1.00** | **1.00** | **1.00** | **1.00** |

Re-sorting lexical's top 5 missed twice. Both times the right doc wasn't in
lexical's top 5 at all (ranks 6 and 8), which is the shortlist-recall limit the
cookbook warns about. That is why the shipped path shortlists 20 and pads a
small corpus with the rest. The frozen thesaurus *hurt* here: it was built
around Part A's topics, which shows how brittle hand-made expansion is.

### Abstention: "no doc governs this"

Lexical search always returns a top hit. Jev's highest probability across all
docs separates the two cases cleanly:

| | AUROC (answerable vs no doc) | at threshold 0.5 |
|---|---|---|
| lexical top score | 0.88 | – (no natural threshold) |
| **Jev max noul** | **1.00** | 8/8 correctly abstained, 0/32 wrongly |

The gap is wide. Unanswerable queries topped out at **0.04**. Answerable ones
never scored below **0.59**.

## Drift: which findings actually matter

`memobank drift` flags a spec whenever a governed file changed after
`last_reviewed`, so all 30 cases are flagged today. Each case is a synthetic diff
against one of six specs from the live corpus. Per spec there is 1 cosmetic
change, 2 behaviour changes the spec allows, and 2 violations of its Restrictions
(one blatant, one subtle). Following the TypeSafe skill's advice, "any
violation" is asked both as one question and as one Noul per Restrictions
bullet, all in a single request.

| method | violations caught | harmless diffs cleared | median latency |
|---|---|---|---|
| today (flag everything) | 12/12 | 0/18 | – |
| Jev, one "any rule broken?" Noul @0.5 | 12/12 | 17/18 | 0.73 s |
| Jev, one Noul per rule, max @0.5 | 12/12 | 17/18 | (same call) |
| Jev, Choice {cosmetic, compliant, violation} | 12/12 | **18/18** | (same call) |
| Sonnet 5 (`claude -p`, reference) | 12/12 | 18/18 | 3.6 s |

AUROC is 1.00 for both Jev signals. **But treat this as "the set was too easy".**
The reference LLM was perfect too, so this set cannot separate a good model from
a great one. The one independent test we know of had Jev catching 6 of 7 planted
defects where Fable 5.1 caught 7. We could not run Fable itself, because it needs
Claude Code ≥ 2.1.251. The per-rule margin is also thin: the highest harmless
diff scored 0.74 and the lowest violation 0.77.

## Cost and latency

| | calls | input tokens | cost |
|---|---|---|---|
| retrieval, both parts (Jev) | 793 | 954k | **$0.040** |
| drift, 30 cases (Jev) | 30 | 44k | **$0.0018** |
| drift, 30 cases (Sonnet 5) | 30 | – | $0.53 |

A Jev call took a median of 0.70 s (p90 0.76 s). With a query's 16 pairs sent in
parallel, a whole-corpus query takes about 0.8 s, against milliseconds for
lexical. Answers were stable: across 40 pairs re-asked live, the largest change
was 0.04, and 29 of 40 were identical.

## Caveats

- **Small and single-corpus.** 9 + 40 queries and 30 diffs, all from one project.
  Directional, not a benchmark.
- **The Part B labeller knew the corpus.** I wrote the queries after reading the
  docs, and the 16 docs cover clearly distinct topics. Jev's perfect Part B score
  is partly an easy set. Part A's labels predate this experiment and are the
  stronger evidence.
- **Scoring every doc scales linearly.** 16 docs means 16 calls per query. The
  shipped path caps this at `--rerank-shortlist` (default 20) lexical candidates.
- **It needs the network and sends data out.** Doc text goes to
  api.typesafe.ai, which is why it is opt-in.

## Outcome

1. **Retrieval: shipped, opt-in** as `memobank serve --rerank jev`; see the main
   README. The `shipped` rows above are the production code path
   (`search_live_reranked`) run over the same cached answers.
2. **Drift: not changed.** Before letting Jev hide findings, build a harder test
   set from real history (actual commits against specs, labelled by a person) and
   rerun it against Fable 5.1. Until then Jev could at most *sort* drift output,
   never suppress it.

## Run it on your own corpus

Put a `jev-ab.yaml` in a data directory. Paths are relative to it:

```yaml
retrieval:
  - name: "my corpus"
    registry: ../.island-slices.json   # the memo-bank registry to search
    queries: queries.yaml              # queries: [{q, expect: [doc-id] | [], hard}]
    expansion: expansion.py            # optional baseline: a module with expand(q)
drift:
  registry: ../.island-slices.json
  cases: drift_cases.yaml              # cases: [{id, spec, label, diff}]
```

```bash
# TYPESAFE_API_KEY from the environment or the repo-root .env
.venv/bin/python eval/jev-ab/retrieval.py    --data path/to/data
.venv/bin/python eval/jev-ab/drift_triage.py --data path/to/data   # --no-llm skips the Sonnet reference
```

Answers are cached in `DATA/cache/`, keyed by a hash of the request. The cache
holds numbers only, so reruns are free and deterministic. Per-query rows go to
`DATA/results/`.
