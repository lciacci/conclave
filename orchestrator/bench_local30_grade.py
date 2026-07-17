#!/usr/bin/env python3
"""Phase-0 head-to-head: local 4-bit Qwen3-Coder-30B (Ollama) vs the H200-served
FP8 Qwen3-Coder-80B, on the frozen hard-30, SAME gpt-5.2 grader that decided the
specialist gate. Reuses divergence.analyse() + the judge_eval scorer verbatim — no
new grading code.

Merges two single-model answer sets into one 2-"model" cache and grades:
  coder80  <- eval_fixtures/eval_candidates_specialist_hard.json  (model "coder")
  coder30  <- eval_candidates_local30_hard.json                   (bench_local30_gen)
The 80B answers are byte-identical to the graded fixture, so with a warm GradeCache
they are cache HITS — you pay only for the 30B's new grades (~$1).

Run (after bench_local30_gen.py):
    export GRADER_URL=https://api.openai.com GRADER_MODEL=gpt-5.2-2025-12-11 \
      GRADER_API_KEY=$(aws ssm get-parameter --name /conclave/grader-api-key \
        --with-decryption --profile yeti-conclave --query Parameter.Value --output text) \
      GRADER_SAMPLES=3
    python3 orchestrator/bench_local30_grade.py

CONFOUNDS (named, not hidden): 4-bit-local vs FP8-served; 30B-A3B vs 80B-A3B are
different models; hard-30 is knowledge-QA, biased toward the bigger model. A 30B
that holds HERE is a strong signal to daily-drive it local. See docs/design.md.
"""
from __future__ import annotations

import json
import os
import statistics
import sys

os.environ.setdefault("CONCLAVE_QUERYSET", "hard")   # analyse() resolves refs from the active set
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_HERE = os.path.dirname(os.path.abspath(__file__))
C30 = os.path.join(_HERE, "eval_candidates_local30_hard.json")
SPEC = os.path.join(_HERE, "eval_fixtures", "eval_candidates_specialist_hard.json")


def merge(c30: dict, spec: dict) -> dict[str, list[dict]]:
    """Build {id: [coder80, coder30]}, dropping any id missing either answer —
    analyse() drops incomplete sets anyway, but doing it here keeps n honest and
    the two singles averaged over the SAME rows."""
    out: dict[str, list[dict]] = {}
    for qid, cands in c30.items():
        c30_content = cands[0]["content"] if cands else None
        coder80 = next((c for c in spec.get(qid, []) if c["model"] == "coder"), None)
        if c30_content is None or coder80 is None or coder80.get("content") is None:
            continue
        out[qid] = [
            {"model": "coder80", "content": coder80["content"], "error": None},
            {"model": "coder30", "content": c30_content, "error": None},
        ]
    return out


def head_to_head(report: dict) -> dict:
    """Per-query win tally from analyse() rows (win = strictly higher score)."""
    wins = {"coder80": 0, "coder30": 0, "tie": 0}
    for r in report.get("per_query", []):
        s = r["scores"]
        if abs(s["coder80"] - s["coder30"]) < 1e-9:
            wins["tie"] += 1
        elif s["coder80"] > s["coder30"]:
            wins["coder80"] += 1
        else:
            wins["coder30"] += 1
    return wins


def _load_and_merge() -> dict:
    if not os.path.exists(C30):
        sys.exit(f"no 30B candidates at {C30} — run bench_local30_gen.py first")
    with open(C30) as f:
        c30 = json.load(f)
    with open(SPEC) as f:
        spec = json.load(f)
    merged = merge(c30, spec)
    if not merged:
        sys.exit("merge produced 0 queries — check the 30B file and the specialist fixture")
    return merged


def demo() -> None:
    """Offline self-check — merge + head_to_head with a canned scorer, no grader."""
    from divergence import analyse

    c30 = {"q1": [{"model": "coder30", "content": "A30"}],
           "q2": [{"model": "coder30", "content": "B30"}],
           "q3": [{"model": "coder30", "content": None, "error": "boom"}]}  # dropped
    spec = {"q1": [{"model": "coder", "content": "A80"}, {"model": "reasoning", "content": "x"}],
            "q2": [{"model": "coder", "content": "B80"}],
            "q3": [{"model": "coder", "content": "C80"}]}
    merged = merge(c30, spec)
    assert set(merged) == {"q1", "q2"}, f"q3 (30B errored) must drop: {set(merged)}"
    assert [c["model"] for c in merged["q1"]] == ["coder80", "coder30"]

    # head_to_head is pure tally logic — assert it directly on canned rows rather
    # than paying a grader (coder30 wins q1, coder80 wins q2, q3 ties -> 1/1/1).
    fake_report = {"per_query": [
        {"scores": {"coder80": 0.6, "coder30": 0.9}},
        {"scores": {"coder80": 0.8, "coder30": 0.4}},
        {"scores": {"coder80": 0.7, "coder30": 0.7}},
    ]}
    h = head_to_head(fake_report)
    assert h == {"coder80": 1, "coder30": 1, "tie": 1}, h
    print("ok — merge (incomplete-drop) + head_to_head tally verified offline")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
        sys.exit()

    from divergence import analyse, print_report
    from judge_eval import (FROZEN_GRADER_SAMPLES, GradeCache, ReferenceGrader,
                            _grader_from_env, frontier_call)

    merged = _load_and_merge()
    base, model, key = _grader_from_env()
    n = int(os.environ.get("GRADER_SAMPLES", str(FROZEN_GRADER_SAMPLES)))
    gc = GradeCache()
    scorer = ReferenceGrader(base, model, key, call=frontier_call, samples=n, cache=gc)
    print(f"grading {len(merged)} queries x 2 (coder80 vs coder30) with {model} @ {base}, "
          f"{n} sample(s)... (80B answers hit the cache; 30B are new)")
    report = analyse(merged, scorer)
    report["grader"] = {"model": model, "url": base, "samples": n}
    print_report(report)

    singles = {m: statistics.fmean([r["scores"][m] for r in report["per_query"]])
               for m in ("coder80", "coder30")}
    wins = head_to_head(report)
    print("\n================ PHASE-0 HEAD-TO-HEAD ================")
    print(f"  n = {len(report['per_query'])} graded queries")
    print(f"  coder80 (FP8, H200)   single = {singles['coder80']:.4f}")
    print(f"  coder30 (4-bit, local) single = {singles['coder30']:.4f}")
    print(f"  delta (80B - 30B)             = {singles['coder80'] - singles['coder30']:+.4f}")
    print(f"  per-query wins: coder80 {wins['coder80']} · coder30 {wins['coder30']} · tie {wins['tie']}")
    print(f"  grader calls: {gc.misses} live (paid), {gc.hits} cached")
    out = os.path.join(_HERE, "eval_bench_local30v80_hard.json")
    with open(out, "w") as f:
        json.dump(report, f, indent=2)
    print(f"  report -> {out}")
    print("  NOTE confounds: 4-bit vs FP8; 30B-A3B vs 80B-A3B; knowledge-QA set biased to bigger.")
