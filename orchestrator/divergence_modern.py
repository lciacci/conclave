#!/usr/bin/env python3
"""Headroom analysis for the MODERN fleet (qwen3-32B / gemma3-27B / mistral-24B), reusing
divergence.analyse() unchanged. The modern candidates answer the SAME hard-30 queries, so the
tested analysis + report apply verbatim — only the candidate file and the fleet differ.

  GRADER_URL/MODEL/API_KEY = a house NEUTRAL to Alibaba/Google/Mistral (gpt-5.2 or sonnet).
  GRADER_SAMPLES=3 for a real CI.
  python3 orchestrator/divergence_modern.py
"""
import os
os.environ.setdefault("CONCLAVE_QUERYSET", "hard")   # must precede the import (sets QUERY_BY_ID)
import json, sys
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from divergence import analyse, print_report
from judge_eval import (FROZEN_GRADER_SAMPLES, GradeCache, ReferenceGrader,
                        _grader_from_env, frontier_call)


def _load_first(*paths):
    """First existing path — working copy, else committed fixture (fresh-clone $0 replay)."""
    for p in paths:
        if os.path.exists(p):
            return p
    return paths[0]

# MODERN_FLEET selects which candidate set: "modern2" (default) is the CORRECTED fleet
# (qwen3-nothink, the real number) — this is the headline result. "modern" is the first run
# where qwen3 was handicapped by a 1024-token budget (kept only so the confound is auditable).
FLEET = os.environ.get("MODERN_FLEET", "modern2")
CANDS = _load_first(os.path.join(_HERE, f"eval_candidates_{FLEET}_hard.json"),
                    os.path.join(_HERE, "eval_fixtures", f"eval_candidates_{FLEET}_hard.json"))
OUT = os.path.join(_HERE, f"eval_divergence_{FLEET}_hard.json")

cache = json.load(open(CANDS))
base, model, key = _grader_from_env()
n = int(os.environ.get("GRADER_SAMPLES", str(FROZEN_GRADER_SAMPLES)))
gc = GradeCache()
scorer = ReferenceGrader(base, model, key, call=frontier_call, samples=n, cache=gc)
# Label by fleet so the emitted JSON is not mislabeled when FLEET != modern (e.g. specialist).
# Unknown fleets fall back to the FLEET key itself rather than a wrong hardcoded string.
_FLEET_LABELS = {
    "modern": "modern: qwen3-32B / gemma3-27B / mistral-24B (3 lineages)",
    "modern2": "modern: qwen3-32B / gemma3-27B / mistral-24B (3 lineages)",
    "specialist": "specialist: Qwen3-Coder-Next-FP8 / DeepSeek-R1-Distill-Qwen-32B / "
                  "Meta-Llama-3.3-70B-AWQ (genuine specialists, 3 lineages)",
}
fleet_label = _FLEET_LABELS.get(FLEET, FLEET)
print(f"{FLEET} fleet | {len(cache)} queries x 3 models | grader {model} @ {base}, {n} samples")
report = analyse(cache, scorer)
report["grader"] = {"model": model, "url": base, "samples": n}
report["fleet"] = fleet_label
print_report(report)
print(f"\ngrader calls: {gc.misses} live (paid), {gc.hits} cached")
json.dump(report, open(OUT, "w"), indent=2)
print(f"saved -> {OUT}")
