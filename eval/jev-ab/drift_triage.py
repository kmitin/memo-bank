#!/usr/bin/env python3
"""Drift arm: can Jev tell which drift findings actually matter?

`memobank drift` flags a spec whenever a governed file changed after
last_reviewed, so every case (a diff touching governed code) is flagged today.
Here each (spec, diff) pair is judged instead. Cases come from the `drift:`
entry of DATA/jev-ab.yaml: `cases:` entries with `id`, `spec` (a doc id in the
registry), `label` (cosmetic | compliant | violation), and `diff`.

  jev-any   one Noul: "does this diff break any rule of the spec?"
  jev-rules one Noul per Restrictions bullet, asked together in one request;
            flagged if any rule's noul crosses the threshold (the skill's advice:
            an "any serious violation" policy needs separate conditions)
  jev-class one Choice {cosmetic, compliant, violation}, same request
  llm       reference: a Claude model via `claude -p` (default Sonnet 5), asked
            for a verdict with a minimal system prompt and no tools

Headline: violation recall (a missed violation is the failure drift exists to
prevent) vs how many harmless diffs are cleared.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
import memo_bank as mb  # noqa: E402
from project_config import load as load_project_config  # noqa: E402
from jev_client import Jev  # noqa: E402
from retrieval import auroc, load_config  # noqa: E402

VIOLATES_ANY = {
    "type": "noul",
    "instructions": (
        "`spec` is the governing contract for the code that `diff` changes. After "
        "applying `diff`, does the code break any rule in `spec.contract` or "
        "`spec.restrictions`?"),
    "criteria": {
        "true": "The change makes the code do something a rule forbids, or stop doing "
                "something a rule requires.",
        "false": "The change is cosmetic, or it changes behaviour in a way the rules "
                 "allow (configuration, new features that follow the rules, "
                 "stricter validation, and so on).",
    },
}

CLASSIFY = {
    "type": "choice",
    "instructions": (
        "`spec` is the governing contract for the code that `diff` changes. Classify "
        "the change relative to `spec`."),
    "criteria": {
        "cosmetic": "Renames, formatting, comments or logging wording; behaviour is unchanged.",
        "compliant": "Behaviour changes, but every rule in the spec still holds.",
        "violation": "After the change, the code breaks at least one rule in the spec.",
    },
}


def rule_question(rule: str) -> dict:
    return {
        "type": "noul",
        "instructions": {
            "rule": rule,
            "question": "`spec` governs the code that `diff` changes. After applying "
                        "`diff`, does the code break `rule`?"},
        "criteria": {
            "true": "The changed code now does what `rule` forbids (or no longer does "
                    "what it requires).",
            "false": "`rule` still holds after the change, or the change does not touch "
                     "what `rule` is about.",
        },
    }


def spec_parts(fed, spec_id: str) -> dict:
    for corpus in fed.slices.values():
        d = corpus.by_id.get(spec_id)
        if d:
            secs = dict(mb._split_sections(mb._load_body(d) or ""))
            get = lambda p: next((v.strip() for h, v in secs.items() if h.startswith(p)), "")
            strip_h = lambda s: re.sub(r"^##.*\n", "", s).strip()
            restrictions = strip_h(get("Restrictions"))
            bullets = [re.sub(r"\s+", " ", b).strip()
                       for b in re.split(r"\n- ", "\n" + restrictions) if b.strip()]
            return {"title": d.title, "contract": strip_h(get("Contract")),
                    "restrictions": restrictions, "_rules": bullets}
    raise KeyError(spec_id)


LLM_PROMPT = """You are reviewing a code change against the spec that governs the changed code.

SPEC: {title}

CONTRACT:
{contract}

RESTRICTIONS:
{restrictions}

DIFF:
{diff}

Does the code, after this diff, break any rule in the spec's Contract or Restrictions?
Cosmetic changes and behaviour changes the rules allow are NOT violations.
Reply with ONLY a JSON object, no prose: {{"violation": true|false, "rule": "<the rule broken, or empty>"}}"""


def ask_llm(spec: dict, diff: str, model: str) -> tuple[bool | None, float, str]:
    prompt = LLM_PROMPT.format(diff=diff, **{k: v for k, v in spec.items() if not k.startswith("_")})
    t0 = time.perf_counter()
    r = subprocess.run(["claude", "-p", "--model", model, "--output-format", "json",
                        "--strict-mcp-config", "--max-turns", "1", "--tools", "",
                        "--system-prompt", "You are a precise code reviewer. Answer in the exact format requested."],
                       input=prompt, capture_output=True, text=True, timeout=300,
                       cwd="/tmp")
    ms = (time.perf_counter() - t0) * 1000
    try:
        out = json.loads(r.stdout)
        text = out.get("result", "")
        verdict = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        return bool(verdict["violation"]), ms, str(out.get("total_cost_usd", ""))
    except Exception:
        return None, ms, (r.stdout + r.stderr)[:300]


def confusion(pred: list[bool], truth: list[bool]) -> dict:
    tp = sum(p and t for p, t in zip(pred, truth))
    fp = sum(p and not t for p, t in zip(pred, truth))
    return {"violations_caught": f"{tp}/{sum(truth)}",
            "harmless_cleared": f"{sum(not p and not t for p, t in zip(pred, truth))}/{truth.count(False)}",
            "false_flags": fp}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, required=True,
                    help="directory holding jev-ab.yaml; cache/ and results/ are written there")
    # Fable 5.1 needs Claude Code >= 2.1.251; Sonnet 5 is the reference on older CLIs
    ap.add_argument("--llm-model", default="claude-sonnet-5")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args()
    data = a.data.expanduser().resolve()
    cfg = load_config(data)["drift"]

    fed = mb.load_federation(load_project_config(cfg["registry"]).slices)
    cases = yaml.safe_load(cfg["cases"].read_text())["cases"]
    specs = {c["spec"]: spec_parts(fed, c["spec"]) for c in cases}
    (data / "cache").mkdir(exist_ok=True)
    (data / "results").mkdir(exist_ok=True)
    jev = Jev(data / "cache" / "drift.json")

    def judge(c):
        sp = specs[c["spec"]]
        state = {"spec": {k: v for k, v in sp.items() if not k.startswith("_")}, "diff": c["diff"]}
        qs = {"any": VIOLATES_ANY, "class": CLASSIFY}
        qs.update({f"rule{i}": rule_question(r) for i, r in enumerate(sp["_rules"])})
        ans = jev.ask(state, qs)
        ms = jev.recorded_ms(state, qs) or 0.0
        # by rule index, not rule text: results stay free of the corpus's wording
        rules = [ans[f"rule{i}"]["noul"] for i in range(len(sp["_rules"]))]
        return {"any": ans["any"]["noul"], "class": ans["class"]["choice"],
                "class_p": ans["class"]["probabilities"], "rules": rules,
                "max_rule": max(rules), "ms": round(ms, 1)}

    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            jres = list(pool.map(judge, cases))
    finally:
        jev.save()

    fres = [None] * len(cases)
    if not a.no_llm:
        fcache_path = data / "cache" / "drift-llm.json"
        fcache = json.loads(fcache_path.read_text()) if fcache_path.exists() else {}

        def llm(c):
            if c["id"] in fcache:
                return fcache[c["id"]]
            v, ms, info = ask_llm(specs[c["spec"]], c["diff"], a.llm_model)
            return {"violation": v, "ms": round(ms), "info": info}
        with ThreadPoolExecutor(max_workers=6) as pool:
            fres = list(pool.map(llm, cases))
        fcache.update({c["id"]: f for c, f in zip(cases, fres) if f["violation"] is not None})
        fcache_path.write_text(json.dumps(fcache, indent=1))

    truth = [c["label"] == "violation" for c in cases]
    rows = []
    for c, j, f in zip(cases, jres, fres):
        rows.append({"id": c["id"], "label": c["label"], "subtle": c.get("subtle"),
                     "breaks": c.get("breaks"), "jev_any": j["any"], "jev_max_rule": j["max_rule"],
                     "jev_class": j["class"], "jev_class_p": j["class_p"],
                     "jev_rules": j["rules"], "jev_ms": j["ms"],
                     "llm": f and f["violation"], "llm_ms": f and f["ms"]})

    summary = {
        "baseline (today: flag everything)": confusion([True] * len(cases), truth),
        "jev-any @0.5": confusion([r["jev_any"] >= .5 for r in rows], truth),
        "jev-rules @0.5": confusion([r["jev_max_rule"] >= .5 for r in rows], truth),
        "jev-class == violation": confusion([r["jev_class"] == "violation" for r in rows], truth),
        "jev-any OR jev-rules @0.5": confusion([max(r["jev_any"], r["jev_max_rule"]) >= .5 for r in rows], truth),
        "auroc": {"jev-any": auroc([r["jev_any"] for r in rows if r["label"] == "violation"],
                                   [r["jev_any"] for r in rows if r["label"] != "violation"]),
                  "jev-rules": auroc([r["jev_max_rule"] for r in rows if r["label"] == "violation"],
                                     [r["jev_max_rule"] for r in rows if r["label"] != "violation"])},
    }
    # the safe operating point: highest threshold that still catches every violation
    for key in ("jev_any", "jev_max_rule"):
        thr = min(r[key] for r in rows if r["label"] == "violation")
        summary[f"{key} @ recall-100% (thr {thr:.3f})"] = confusion([r[key] >= thr for r in rows], truth)
    if not a.no_llm:
        summary["llm:" + a.llm_model] = confusion([bool(r["llm"]) for r in rows], truth)
        summary["llm_unparsed"] = sum(r["llm"] is None for r in rows)
        fms = [r["llm_ms"] for r in rows if r["llm_ms"]]
        summary["llm_ms_median"] = round(statistics.median(fms)) if fms else None
    summary["jev_ms_median"] = round(statistics.median(r["jev_ms"] for r in rows), 1)
    summary["jev"] = {"model": jev.served_model or jev.model, "live_calls": jev.live_calls,
                      "input_tokens": jev.input_tokens, "cost_usd": round(jev.cost_usd(), 5)}

    (data / "results" / "drift.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    print(json.dumps(summary, indent=1))
    print("\nper case:  id | label | jev-any | max-rule | class | llm")
    for r in rows:
        print(f"  {r['id']:9} {r['label'][:4]}{'*' if r['subtle'] else ' '} "
              f"{r['jev_any']:.2f}  {r['jev_max_rule']:.2f}  {r['jev_class']:9} {r['llm']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
