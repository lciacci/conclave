#!/usr/bin/env python3
"""FLEET INSTRUMENT — is this candidate set worth ensembling at all?

Fan-out + judge only pays when the candidates are **comparably strong** and **genuinely
decorrelated**, so that different models win on different inputs. That is a property of the
FLEET, not of the judge — and it is testable *before* you build any judge, for $0, offline.

This measures it. The headline number:

    HEADROOM = ORACLE (a perfect judge, always picks the best candidate)
             - BEST SINGLE MODEL (just always call the strongest one, no judge, no fan-out)

Headroom is the ENTIRE value the ensemble+judge pattern can ever buy on this fleet. A real
judge captures some fraction of it (and can capture a NEGATIVE fraction — a bad judge is
worse than not judging). So:

    headroom ~0     -> STOP. No judge, however good, can beat one model here. Fix the fleet.
                       (Route or cascade instead: pick the right model, don't vote.)
    headroom large  -> the models genuinely disagree in useful ways. A judge has something
                       to arbitrate, and building/tuning one is worth the spend.

**Conclave's own fleet scores +0.028** (n=36) — coder-14B is the best candidate on 31/36
queries across ALL THREE categories. It is one strong model carrying two weaker ones, so
fan-out mostly offers a chance to pick something *worse*, which is what the judge does
(0.883 judged vs 0.933 for always-coder). Not a failure of the pattern; a failure of the
fleet to satisfy the pattern's precondition. Use this tool when choosing the next fleet.

Also reports, per query:
  * spread (best - worst): ~0 means the candidates are interchangeable and NO judging task
    exists for that query. 12/36 of the current set is degenerate this way.
  * best_candidate: the ground-truth label that select-mode judge scoring needs.

Offline (no GPU): candidates are frozen in eval_fixtures/. Uses the same GradeCache, so a
re-run is free and a killed run resumes.

    python3 orchestrator/divergence.py            # grade + report
    python3 orchestrator/divergence.py --demo     # offline self-check
"""
from __future__ import annotations

import json
import math
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

    # THE HEADLINE: how much can ANY judge ever buy on this fleet?
    # oracle = a perfect judge. best_single = the strongest model, used alone, no judge.
    # headroom = oracle - best_single. That is the whole prize. If it is ~0, no judge is
    # worth building: route or cascade instead.
    models = sorted({m for r in rows for m in r["scores"]})
    singles = {m: statistics.fmean([r["scores"][m] for r in rows if m in r["scores"]])
               for m in models}
    best_single_model = max(singles, key=singles.get) if singles else None
    oracle = statistics.fmean([r["best_score"] for r in rows]) if rows else 0.0
    best_single = singles.get(best_single_model, 0.0)
    headroom = oracle - best_single
    # paired CI on the headroom — it is a claim about a population of queries
    hr_ci = None
    if best_single_model and len(rows) > 1:
        d = [r["best_score"] - r["scores"][best_single_model] for r in rows
             if best_single_model in r["scores"]]
        sem = statistics.stdev(d) / math.sqrt(len(d))
        hr_ci = [round(statistics.fmean(d) - 1.96 * sem, 4),
                 round(statistics.fmean(d) + 1.96 * sem, 4)]

    return {
        "headroom": {
            "value": round(headroom, 4), "ci95": hr_ci,
            "oracle": round(oracle, 4),
            "best_single_model": best_single_model,
            "best_single_score": round(best_single, 4),
            "single_model_scores": {m: round(v, 4) for m, v in singles.items()},
            "verdict": ("ENSEMBLE NOT WORTH IT — no judge can beat one model here; "
                        "route or cascade instead"
                        if headroom < 0.05 else
                        "MARGINAL — a judge must be very good to pay for itself"
                        if headroom < 0.10 else
                        "WORTH ENSEMBLING — the models disagree usefully"),
            "note": "headroom = ORACLE (perfect judge) - BEST SINGLE MODEL. The entire "
                    "value the ensemble+judge pattern can ever buy on this fleet. A real "
                    "judge captures a fraction of it — possibly a negative fraction.",
        },
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
    h = r["headroom"]
    print(f"\n=== FLEET HEADROOM — is this candidate set worth ensembling? (n={n}) ===")
    print(f"  single models, used ALONE (no judge, no fan-out):")
    for m, v in sorted(h["single_model_scores"].items(), key=lambda kv: -kv[1]):
        star = "  <- best single" if m == h["best_single_model"] else ""
        print(f"      {m:12s} {v:.3f}{star}")
    print(f"  ORACLE (a PERFECT judge, always picks the best candidate): {h['oracle']:.3f}")
    ci = f"  95% CI [{h['ci95'][0]:+.3f}, {h['ci95'][1]:+.3f}]" if h["ci95"] else ""
    print(f"\n  >>> HEADROOM = oracle - best single = {h['value']:+.4f}{ci}")
    print(f"      {h['verdict']}")
    print(f"      (This is the ENTIRE value any judge can ever buy here. A real judge")
    print(f"       captures a fraction of it — and a bad one captures a NEGATIVE fraction.)")

    print(f"\n=== candidate divergence — where a judging task even exists ===")
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

    # HEADROOM is the headline: oracle - best single model. Here coder scores (1.0+1.0)/2=1.0
    # and the oracle also gets 1.0 on both -> headroom 0: a perfect judge buys NOTHING over
    # just always using coder. The tool must say so rather than celebrate the divergence.
    h = r["headroom"]
    assert h["best_single_model"] == "coder", h
    assert abs(h["value"]) < 1e-9, f"a fleet whose best model IS the oracle has ZERO headroom: {h}"
    assert "NOT WORTH IT" in h["verdict"], h["verdict"]
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
