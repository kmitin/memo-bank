# Does retrieval actually save tokens? An A/B

Short answer, on the corpus we measured: **no measurable saving — and the reason is
instructive.** This directory holds the method, the raw numbers, and the honest
conclusion, so the claim can be re-tested rather than taken on faith.

## Method

Two arms, same repo, same 8 questions of the form *"what rules govern `<path>`?
Name the governing spec and state its restrictions."*

| arm | setup |
|---|---|
| **retrieval** | memo-bank MCP server available + the `memo-bank-query` skill |
| **control** | MCP stripped (`--strict-mcp-config`), skill moved out of the skills tree; the corpus is still on disk, so the agent must find it by searching |

8 tasks × 2 repetitions × 2 arms = **32 headless runs** (`claude -p --output-format json`),
recording turns, token usage by cache class, and cost. Every run was graded on whether
it named the correct governing spec. Raw per-run numbers are in `results.csv`.

## Results

**All 32 runs named the correct spec.** Retrieval did not improve correctness here —
the control could always find the answer too.

| | retrieval | control | delta |
|---|---|---|---|
| raw tokens / task | 346,756 | 359,164 | +3.5% |
| cache-weighted tokens / task | 71,908 | 76,751 | +6.3% |
| turns / task | 10.1 | 11.1 | +9.0% |
| cost / task | $0.458 | $0.480 | +4.6% |

Per-task token deltas: `-18% -11% -6% -6% +2% +13% +19% +21%` — retrieval was cheaper
on **4 of 8** tasks; mean +1.7%, sd **14.4pp**. The effect is indistinguishable from
zero at this sample size.

> A first pass with only one repetition showed −20.6% and looked like a clear win.
> The second repetition reversed it. Reporting after one rep would have published a
> number that was an artefact of sampling.

### The one robust difference: variance

| | tokens sd | turns range | spread |
|---|---|---|---|
| retrieval | 46,832 | 7–12 | 1.9× |
| control | 123,300 | 3–18 | **6.0×** |

Search is a lottery — sometimes it lands in 3 turns, sometimes it wanders for 18.
Retrieval is boringly consistent. **Predictable** context cost, not **lower** context cost.

## Why the null result: corpus size

92% of every run is `cache_read` — the per-turn context reload. Cached reads cost ~0.1×,
so carrying more context per turn is cheap, which makes an obvious alternative viable:
**skip retrieval and put the whole corpus in the cached prefix.**

Pricing that alternative against the measured retrieval cost (71,908 weighted):

| corpus tokens | preload cost (2 turns) | winner |
|---|---|---|
| 10,000 | 52,593 | preload |
| **20,364** *(the corpus we measured)* | **67,621** | **preload** |
| 30,000 | 81,593 | retrieval |
| 100,000 | 183,093 | retrieval |
| 500,000 | 763,093 | retrieval |

**Break-even is ~20–23k corpus tokens, and the measured corpus was 20,364.** The
experiment was run precisely at the crossover, where nothing can beat loading
everything. It was structurally incapable of showing a win.

## What this means

- **Below ~20k tokens of corpus, don't reach for retrieval.** Put the specs in the
  system prompt and let prompt caching carry them.
- **Retrieval should pay off as the corpus outgrows the context budget.** That is a
  prediction this experiment has not yet tested — the obvious next run is a corpus
  5–10× larger.
- Retrieval costs a small permanent tax: the MCP tool definitions add **~556 tokens
  per run** of prefix.
- The durable benefits demonstrated here are **predictability** and the maintenance
  loops (coverage, drift) — not token savings.

## Reproducing

`sweep.sh` is the harness. Point it at a repo with a memo-bank corpus, list your tasks
in `tasks.tsv` (`tag<TAB>slice<TAB>path<TAB>expected-spec-id`), then:

```bash
sh sweep.sh retrieval 1 && sh sweep.sh control 1
sh sweep.sh retrieval 2 && sh sweep.sh control 2
```

Cost is roughly $0.45–0.50 per run, so a full 8×2×2 sweep is about $15 and 20 minutes.
`results.csv` holds token counts only — no repo content — so it is safe to publish.
