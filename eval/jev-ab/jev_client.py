"""Caching wrapper for the eval around the production client (rerank.JevClient).

Every answer is cached by a hash of (model, state, question), and the cache
stores only hashes and numbers, never request text. So the cache can be
committed without leaking the corpus, and a rerun without a key replays it.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from rerank import JevClient  # noqa: E402

PRICE_IN_PER_M = 0.042  # $ per 1M input tokens; output is free (jev, 2026-09)


def load_env(path: Path = HERE.parent.parent / ".env") -> None:
    if not path.exists():
        return
    for ln in path.read_text().splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, v = ln.split("=", 1)
            v = v.strip().strip("'\"")  # KEY="value" is valid dotenv; the shell strips quotes too
            if v:
                os.environ.setdefault(k.strip(), v)


class Jev:
    def __init__(self, cache_path: Path, model: str = "jev-latest"):
        load_env()
        self.key = os.environ.get("TYPESAFE_API_KEY")
        self.model = model
        self.cache_path = cache_path
        self.cache: dict = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        self.lock = threading.Lock()
        self.live_calls = 0
        self.input_tokens = 0
        self.latencies_ms: list[float] = []
        self.served_model: str | None = None

    def _key(self, state, questions) -> str:
        blob = json.dumps([self.model, state, questions], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def ask(self, state, questions: dict) -> dict:
        """Returns {question_id: answer}. Cached answers skip the network."""
        k = self._key(state, questions)
        with self.lock:
            if k in self.cache:
                return self.cache[k]["answers"]
        if not self.key:
            raise RuntimeError("no TYPESAFE_API_KEY and answer not in cache")
        t0 = time.perf_counter()
        resp = JevClient(api_key=self.key, model=self.model, attempts=6).ask(state, questions)
        ms = (time.perf_counter() - t0) * 1000
        # keep only numbers: no request text goes into the cache
        answers = {qid: {f: a[f] for f in ("type", "noul", "choice", "score",
                                            "probabilities", "confidence") if f in a}
                   for qid, a in resp["answers"].items()}
        with self.lock:
            self.live_calls += 1
            self.input_tokens += resp.get("usage", {}).get("input_tokens", 0)
            self.latencies_ms.append(ms)
            self.served_model = resp.get("model")
            self.cache[k] = {"answers": answers, "model": resp.get("model"),
                             "input_tokens": resp.get("usage", {}).get("input_tokens", 0),
                             "ms": round(ms, 1)}
        return answers

    def recorded_ms(self, state, questions) -> float | None:
        """Latency of the live call that produced this (possibly cached) answer."""
        e = self.cache.get(self._key(state, questions))
        return e and e.get("ms")

    def save(self) -> None:
        self.cache_path.write_text(json.dumps(self.cache, indent=0, sort_keys=True))

    def cost_usd(self) -> float:
        return self.input_tokens * PRICE_IN_PER_M / 1e6
