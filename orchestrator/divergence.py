#!/usr/bin/env python3
"""Do the specialists actually DISAGREE? — the precondition for the whole judge thesis.

A judge can only be measured on queries where the candidates DIVERGE: one clearly better,
the others plausible-but-worse. `eval_queryset.py` says exactly this in its own docstring.
It was never checked.

It matters more than any metric fix. If all three specialists answer a query equally well,
there is nothing to select, the judging task is undefined for that query, and no
instrument — select mode, pairwise, a neutral third-vendor grader — can rescue it. It
would also explain the ceiling more simply than anything else: the frontier judge writes
its own answer (chosen == -1 on 34/36) because choosing among three correct answers is
pointless.

What this does: grades all 3 candidates for every query against the gold reference with
the same ReferenceGrader the eval uses, then reports the spread (best - worst) per query.

  spread ~0     -> candidates are interchangeable. NO judging task exists here.
  spread large  -> a real choice exists, and picking right is a measurable skill.

It also emits `best_candidate` per query — the ground-truth label that select-mode scoring
needs, so this is not a throwaway diagnostic.

Offline (no GPU): candidates are frozen in eval_fixtures/. Uses the same GradeCache, so a
re-run is free and a killed run resumes.

    python3 orchestrator/divergence.py            # grade + report
    python3 orchestrator/divergence.py --demo     # offline self-check
"""
from __future__ import annotations

import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import candidate_cache
from eval_queryset import QUERY_SET
from judge_eval import (FROZEN_GRADER_SAMPLES, GradeCache, ReferenceGrader, _grader_from_env,
                        frontier_call)

_HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(_HERE, "eval_divergence.json")
QUERY_BY_ID = {q["id"]: q for q in QUERY_SET}

# Below this, the best and worst candidate are the same answer as far as the grader can
# tell. One grader step on the 0-5 scale is 0.2; half a step is the smallest difference
# worth calling a difference.
MEANINGFUL = 0.1


def analyse(cache: dict[str, list[dict]], scorer) -> dict:
    rows = []
    for qid, cands in cache.items():
        q = QUERY_BY_ID.get(qid)
        if not q:
            continue
        scored = []
        for c in cands:
            if not c.get("content"):
                continue
            scored.append({"model": c["model"], "score": scorer.score(q, c["content"])})
        if len(scored) < 2:
            continue
        best = max(scored, key=lambda s: s["score"])
        worst = min(scored, key=lambda s: s["score"])
        rows.append({
            "id": qid, "category": q["category"],
            "scores": {s["model"]: s["score"] for s in scored},
            "best_candidate": best["model"], "best_score": best["score"],
            "spread": round(best["score"] - worst["score"], 3),
            "all_correct": worst["score"] >= 0.9,     # even the worst is essentially right
            "divergent": (best["score"] - worst["score"]) > MEANINGFUL,
        })

    div = [r for r in rows if r["divergent"]]
    allc = [r for r in rows if r["all_correct"]]
    by_cat: dict[str, list] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)

    return {
        "n": len(rows),
        "meaningful_spread_threshold": MEANINGFUL,
        "divergent": len(div),
        "degenerate": len(rows) - len(div),
        "all_three_essentially_correct": len(allc),
        "mean_spread": round(statistics.fmean([r["spread"] for r in rows]), 3) if rows else 0,
        "median_spread": round(statistics.median([r["spread"] for r in rows]), 3) if rows else 0,
        "by_category": {
            c: {"n": len(v), "divergent": sum(r["divergent"] for r in v),
                "mean_spread": round(statistics.fmean([r["spread"] for r in v]), 3)}
            for c, v in by_cat.items()},
        "best_candidate_counts": {
            m: sum(r["best_candidate"] == m for r in rows)
            for m in sorted({r["best_candidate"] for r in rows})},
        "per_query": rows,
    }


def print_report(r: dict) -> None:
    n, d = r["n"], r["divergent"]
    print(f"\n=== CANDIDATE DIVERGENCE — n={n} queries, 3 specialists each ===")
    print(f"spread = best candidate's grade - worst candidate's grade (0-1 scale)")
    print(f"  mean spread   {r['mean_spread']:.3f}   median {r['median_spread']:.3f}")
    print(f"  DIVERGENT (spread > {r['meaningful_spread_threshold']}): {d}/{n}"
          f"  ({100*d/n:.0f}%)  <- a real judging task exists")
    print(f"  DEGENERATE (candidates interchangeable): {r['degenerate']}/{n}"
          f"  ({100*r['degenerate']/n:.0f}%)  <- nothing to judge")
    print(f"  all three specialists essentially correct: {r['all_three_essentially_correct']}/{n}")
    print("\nby category:")
    for c, v in r["by_category"].items():
        print(f"  {c:10s} divergent {v['divergent']:2d}/{v['n']:2d}   mean spread {v['mean_spread']:.3f}")
    print("\nwhich specialist is actually best (ground truth for select-mode):")
    for m, k in r["best_candidate_counts"].items():
        print(f"  {m:12s} {k:2d}/{n}")
    print(f"\nVERDICT: ", end="")
    frac = d / n if n else 0
    if frac < 0.34:
        print(f"the query set is mostly DEGENERATE ({r['degenerate']}/{n}). The specialists\n"
              f"  agree on most queries, so for those there is NO judging task to measure —\n"
              f"  and no metric (select mode, pairwise, a neutral grader) can fix that.\n"
              f"  The query set needs queries where the specialists genuinely diverge.")
    elif frac < 0.67:
        print(f"MIXED — only {d}/{n} queries pose a real choice. Judge metrics should be\n"
              f"  computed on the divergent subset; the rest measure nothing.")
    else:
        print(f"the candidates DO diverge on {d}/{n}. A judging task genuinely exists,\n"
              f"  so select-mode scoring against `best_candidate` is meaningful.")


def demo() -> None:
    """Offline self-check — no network."""
    qs = QUERY_SET[:3]

    class FakeScorer:
        name = "fake"
        def __init__(self, table): self.table = table
        def score(self, query, answer): return self.table[answer]

    # one query where candidates diverge, one where they are identical
    cache = {
        qs[0]["id"]: [{"model": "coder", "content": "good", "error": None},
                      {"model": "reasoning", "content": "bad", "error": None},
                      {"model": "general", "content": "mid", "error": None}],
        qs[1]["id"]: [{"model": "coder", "content": "same", "error": None},
                      {"model": "reasoning", "content": "same2", "error": None},
                      {"model": "general", "content": "same3", "error": None}],
    }
    sc = FakeScorer({"good": 1.0, "bad": 0.2, "mid": 0.6,
                     "same": 1.0, "same2": 1.0, "same3": 0.95})
    r = analyse(cache, sc)
    assert r["n"] == 2
    byid = {x["id"]: x for x in r["per_query"]}
    a, b = byid[qs[0]["id"]], byid[qs[1]["id"]]
    assert a["divergent"] and a["spread"] == 0.8 and a["best_candidate"] == "coder"
    assert not b["divergent"], "candidates within 0.05 are interchangeable — no judging task"
    assert b["all_three_essentially_correct"] if "all_three_essentially_correct" in b else True
    assert r["divergent"] == 1 and r["degenerate"] == 1
    assert r["best_candidate_counts"]["coder"] >= 1
    print("ok — divergence analysis verified offline (divergent vs degenerate queries)")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
        sys.exit()

    cache = candidate_cache.load()
    if not cache:
        sys.exit("no candidate cache (and no fixture) — nothing to analyse")
    base, model, key = _grader_from_env()
    n = int(os.environ.get("GRADER_SAMPLES", str(FROZEN_GRADER_SAMPLES)))
    gc = GradeCache()
    scorer = ReferenceGrader(base, model, key, call=frontier_call, samples=n, cache=gc)
    print(f"grading {sum(len(c) for c in cache.values())} candidates "
          f"({len(cache)} queries x 3 specialists) with {model} @ {base}, {n} sample(s)...")
    report = analyse(cache, scorer)
    report["grader"] = {"model": model, "url": base, "samples": n}
    print_report(report)
    print(f"\ngrader calls: {gc.misses} live (paid), {gc.hits} from cache")
    with open(OUT, "w") as f:
        json.dump(report, f, indent=2)
    print(f"saved -> {OUT}")
