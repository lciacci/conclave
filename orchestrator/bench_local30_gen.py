#!/usr/bin/env python3
"""Phase-0 local-first benchmark: generate Qwen3-Coder-30B-A3B candidates on the
frozen hard-30, LOCALLY via Ollama's OpenAI-compatible endpoint — $0, no GPU boot.

LOCAL_CODER defaults to "qwen3-coder:30b" if not set in environment.

WHY LOCAL. The 30B-A3B is MoE (~3B active), ~18GB at 4-bit, and runs on the 64GB
Mac. If its quality holds vs the H200-served 80B (eval_candidates_specialist_hard,
coder 0.949), it deploys on the laptop for $0 — so the daily driver may never need
a rented GPU. This measures that, reusing http_call (no new deps).

CONFOUND, stated on purpose: this is Ollama 4-bit vs the 80B's FP8, and the hard-30
is knowledge-QA (biased toward the bigger model — see design.md). So a 30B that
holds HERE is a strong signal; the real-task check comes when it is wired as a
daily driver.

Run (Ollama up, model pulled):
    python3 orchestrator/bench_local30_gen.py
    # -> eval_candidates_local30_hard.json  {id: [{model:"coder30", content, ...}]}
Then merge + grade with bench_local30_grade.py.

Options:
    --help     Show this help message and exit
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ensemble import http_call
from eval_queryset_hard import HARD_QUERY_SET

OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://localhost:11434")
MODEL = os.environ.get("LOCAL_CODER", "qwen3-coder:30b")
MAX_TOKENS = int(os.environ.get("CONCLAVE_MAX_TOKENS", "8192"))  # match the 80B run
TIMEOUT = float(os.environ.get("CONCLAVE_TIMEOUT", "600"))       # local can be slow
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "eval_candidates_local30_hard.json")


def generate() -> dict[str, list[dict]]:
    # Resume from any partial file: a crash mid-run keeps finished answers.
    cache: dict[str, list[dict]] = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            cache = json.load(f)
    for q in HARD_QUERY_SET:
        if q["id"] in cache:
            continue
        msgs = [{"role": "user", "content": q["prompt"]}]
        t0 = time.monotonic()
        try:
            content = http_call(OLLAMA_BASE, MODEL, msgs, TIMEOUT, max_tokens=MAX_TOKENS)
            err = None
        except Exception as e:
            content, err = None, f"{type(e).__name__}: {e}"
        rec = {"model": "coder30", "content": content,
               "latency_s": round(time.monotonic() - t0, 3), "error": err}
        if content is None:
            print(f"  {q['id']}: ERROR {err}", file=sys.stderr)
        else:
            cache[q["id"]] = [rec]
            with open(OUT, "w") as f:               # persist incrementally
                json.dump(cache, f, indent=2, ensure_ascii=False)
            print(f"  {q['id']}: {len(content)} chars, {rec['latency_s']}s")
    return cache


def demo() -> None:
    """Offline self-check — no Ollama, canned call. Proves shape + resume."""
    import tempfile
    global OUT
    OUT = os.path.join(tempfile.mkdtemp(), "c.json")
    import ensemble
    real = ensemble.http_call
    ensemble.http_call = lambda b, m, msg, t, **k: f"answer to {msg[-1]['content'][:12]}"
    # rebind the name this module imported
    globals()["http_call"] = ensemble.http_call
    try:
        c = generate()
        assert len(c) == len(HARD_QUERY_SET), "all queries generated"
        assert all(len(v) == 1 and v[0]["model"] == "coder30" for v in c.values())
        # resume: a second run regenerates nothing
        n_before = os.path.getmtime(OUT)
        time.sleep(0.01)
        generate()
        assert os.path.getmtime(OUT) == n_before, "resume: no rewrite when complete"
        print(f"ok — gen shape + resume verified offline ({len(c)} queries)")
    finally:
        ensemble.http_call = real
        globals()["http_call"] = real


if __name__ == "__main__":
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__.strip())
        sys.exit(0)
    elif "--demo" in sys.argv:
        demo()
    else:
        print(f"generating {MODEL} on {len(HARD_QUERY_SET)} hard queries "
              f"via {OLLAMA_BASE} (max_tokens={MAX_TOKENS}) ...")
        cache = generate()
        ok = sum(1 for v in cache.values() if v[0]["content"] is not None)
        print(f"done — {ok}/{len(HARD_QUERY_SET)} generated -> {OUT}")
