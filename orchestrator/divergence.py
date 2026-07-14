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

## THE LESSON THIS TOOL EXISTS TO TEACH: **disagreement is cheap; COMPLEMENTARITY is rare.**

Conclave's fleet was measured TWICE, and the pair is the whole point:

    n=36 easy queries : headroom +0.028, but 31/36 were AT THE GRADER'S CEILING
                        -> the verdict was UNDECIDABLE, not negative.
    n=30 HARD queries : ceiling collapses 86% -> 20%. Scores fall 0.933 -> 0.696.
                        Ties fall 78% -> 40%. The models now DISAGREE on 80% of
                        queries. AND HEADROOM DOES NOT MOVE: +0.027.

**The ceiling was hiding nothing.** Removing it TRIPLED the disagreement and changed the
answer not at all. Why? Because **divergence is NOT headroom**:

    coder 0.696  |  general 0.527  |  reasoning 0.518
    strict wins: coder 12/30, reasoning 4, general 2
    coder is SIGNIFICANTLY best: margin CI [+0.084, +0.254]

The models disagree constantly — but **when they disagree, coder is usually the one that is
RIGHT.** That is HIERARCHY, not complementarity. A *perfect* oracle judge therefore barely
beats "always call coder", and a real judge does worse.

**AND THE ORACLE BOUNDS ROUTERS TOO — so "route instead of judging" is NOT the escape.**
The oracle is perfect PER-QUERY SELECTION. A judge selects after seeing the answers; a
router selects seeing only the QUERY, so it has strictly LESS information:

    router  <=  judge  <=  oracle  =  best_single + headroom

A perfect router therefore also buys at most +0.027 here, and a real one buys less. Headroom
does not merely condemn the judge — **it condemns EVERY selection policy over this fleet.**
The honest conclusion is not "route, don't judge". It is: **just call the strongest model.**
Routing is only the cheaper way to chase a prize that is not there.

And hierarchy is the DEFAULT, not a quirk of this fleet: any fleet with one genuinely
stronger member behaves this way, and a 14B coder simply IS better than a 7B reasoner and a
9B general model at most tasks. **You cannot tell by looking — a fleet can disagree loudly
and still be worthless to ensemble.** That is exactly why this tool runs BEFORE the judge.

What this does NOT show: that fan-out+judge never pays. It shows ONE fleet failing the
pattern's PRECONDITION. A fleet of comparable-strength, genuinely different-lineage models
may well have real headroom — and it must clear a high bar, because it has to beat a router
that costs 1x inference instead of 3x + a judge + the measured ~30% contention tax.

TWO TRAPS THIS TOOL EXISTS TO AVOID — both were live, both produced published nonsense:

  * **TIES ARE NOT WINS.** `max()` returns the FIRST max. With candidates ordered
    [coder, reasoning, general], every tie was credited to coder — which is how
    "coder is the best candidate on 31/36 queries" got published. Reverse the list and the
    same data says `general` wins 27. On STRICT wins it is **coder 4 / general 4 /
    reasoning 0**. Report `strict_win_counts`, never a tie-blind argmax.
  * **The CI's lower bound is vacuous.** headroom >= 0 BY CONSTRUCTION (a max is never below
    any of its members), so "the CI excludes zero" tests an impossible null and is
    guaranteed. Judge the UPPER bound against the verdict threshold — and if the threshold
    lies inside the CI, say the verdict is NOT RESOLVED rather than picking one.

Also reports, per query:
  * spread (best - worst): ~0 means the candidates are interchangeable and NO judging task
    exists for that query.
  * best_candidates (a LIST — ties included) + strict_winner (None on a tie). Only strict
    winners are usable as select-mode ground truth; a tied query has no right answer.
  * at_ceiling: the grader is already maxed, so headroom there is 0 by construction.

Offline (no GPU): candidates are frozen in eval_fixtures/. Uses the same GradeCache, so a
re-run is free and a killed run resumes.

    python3 orchestrator/divergence.py            # grade + report
    python3 orchestrator/divergence.py --demo     # offline self-check
"""
from __future__ import annotations

import contextlib
import io
import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import candidate_cache
from eval_queryset import active_query_set, active_set_name
from judge_eval import (FROZEN_GRADER_SAMPLES, GradeCache, ReferenceGrader, _grader_from_env,
                        frontier_call)

_HERE = os.path.dirname(os.path.abspath(__file__))
SET_NAME = active_set_name()
QUERY_SET = active_query_set(SET_NAME)
OUT = os.path.join(_HERE, "eval_divergence.json" if SET_NAME == "base"
                   else f"eval_divergence_{SET_NAME}.json")
QUERY_BY_ID = {q["id"]: q for q in QUERY_SET}

# Below this, the best and worst candidate are the same answer as far as the grader can
# tell. One grader step on the 0-5 scale is 0.2; half a step is the smallest difference
# worth calling a difference.
MEANINGFUL = 0.1


def analyse(cache: dict[str, list[dict]], scorer) -> dict:
    rows = []
    skipped_incomplete: list[str] = []
    skipped_ungraded: list[str] = []
    for qid, cands in cache.items():
        q = QUERY_BY_ID.get(qid)
        if not q:
            continue
        # EVERY candidate must have answered, or the row is dropped. Scoring a query where
        # only 2 of 3 replied would average `singles[m]` over a different row set than
        # `oracle`, and the denominators drift: blanking one losing answer for a model
        # RAISES its single-model score and collapses the headroom. A fleet error would
        # silently make the ensemble look less useful.
        if any(not c.get("content") for c in cands) or len(cands) < 2:
            skipped_incomplete.append(qid)
            continue
        # score() may return None — the grader gave no usable verdict (refusal, content
        # filter, max-tokens cutoff). None is NOT a score of zero, and it must not reach
        # max()/fmean(), which would crash mid-run *after* the grades were paid for.
        scored = [{"model": c["model"], "score": scorer.score(q, c["content"])} for c in cands]
        if any(s["score"] is None for s in scored):
            skipped_ungraded.append(qid)
            continue

        top = max(s["score"] for s in scored)
        bot = min(s["score"] for s in scored)
        # TIES ARE NOT WINS. max() returns the FIRST max, so with candidates ordered
        # [coder, reasoning, general] every tie was silently credited to coder — which is
        # how "coder is the best candidate on 31/36" got published. It was a statement
        # about the order of a list in a config file: reverse the list and `general` wins
        # 27. On STRICT (unique) wins it is coder 4 / general 4 / reasoning 0.
        winners = [s["model"] for s in scored if s["score"] >= top - 1e-9]
        rows.append({
            "id": qid, "category": q["category"],
            "scores": {s["model"]: s["score"] for s in scored},
            "best_candidates": winners,                       # ALL models tied at the top
            "strict_winner": winners[0] if len(winners) == 1 else None,  # None = a tie
            "tied_at_top": len(winners) > 1,
            "best_score": top,
            "spread": round(top - bot, 3),
            "all_correct": bot >= 0.9,     # even the worst is essentially right
            "divergent": (top - bot) > MEANINGFUL,
            "at_ceiling": top >= 0.999,    # grader saturated: no headroom possible here
        })

    # A query in the SET but absent from the CACHE was never generated — the fleet never
    # answered it. analyse() iterates the cache, not the query set, so such a query used to
    # vanish in silence: you could add 30 hard queries, run this, and read a confident
    # `n=36` report about the OLD set without ever noticing the new ones were never run.
    # The candidate cache is only populated by a GPU boot, so this WILL happen. Say it.
    missing = [qid for qid in QUERY_BY_ID if qid not in cache]

    div = [r for r in rows if r["divergent"]]
    allc = [r for r in rows if r["all_correct"]]
    by_cat: dict[str, list] = {}
    for r in rows:
        by_cat.setdefault(r["category"], []).append(r)

    # THE HEADLINE: how much can ANY judge ever buy on this fleet?
    # oracle = a perfect judge. best_single = the strongest model, used alone, no judge.
    # headroom = oracle - best_single. That is the whole prize.
    models = sorted({m for r in rows for m in r["scores"]})
    singles = {m: statistics.fmean([r["scores"][m] for r in rows]) for m in models}
    best_single_model = max(singles, key=singles.get) if singles else None
    oracle = statistics.fmean([r["best_score"] for r in rows]) if rows else 0.0
    best_single = singles.get(best_single_model, 0.0)
    headroom = oracle - best_single

    hr = {}
    if best_single_model and len(rows) > 1:
        d = [r["best_score"] - r["scores"][best_single_model] for r in rows]
        sem = statistics.stdev(d) / math.sqrt(len(d))
        lo, hi = statistics.fmean(d) - 1.96 * sem, statistics.fmean(d) + 1.96 * sem
        # The lower bound is NOT evidence. headroom >= 0 BY CONSTRUCTION (a max is never
        # below any of its members), so "the CI excludes zero" tests an impossible null
        # and is guaranteed the moment any model beats the best one even once. The bound
        # that decides anything is the UPPER one, against the verdict threshold.
        hr["ci95"] = [round(lo, 4), round(hi, 4)]
        hr["ci_note"] = ("Lower bound is vacuous: headroom >= 0 by construction, so "
                         "'excludes 0' is guaranteed and is NOT evidence. Judge the "
                         "UPPER bound against the verdict threshold.")
        # Is the best single model even significantly the best? If not, the whole verdict
        # is hostage to an arbitrary pick — a different 'best single' changes the answer.
        runner = sorted(singles, key=singles.get, reverse=True)
        if len(runner) > 1:
            dm = [rw["scores"][runner[0]] - rw["scores"][runner[1]] for rw in rows]
            msem = statistics.stdev(dm) / math.sqrt(len(dm))
            m_lo = statistics.fmean(dm) - 1.96 * msem
            hr["best_single_is_significant"] = bool(m_lo > 0)
            hr["best_single_margin_ci95"] = [round(m_lo, 4),
                                             round(statistics.fmean(dm) + 1.96 * msem, 4)]
            hr["runner_up"] = runner[1]

    # Ceiling: on queries where the best candidate already scores 1.0, headroom is ZERO by
    # construction — the grader cannot go higher. Reporting headroom without saying how
    # much of the query set is saturated hides that the number rests on a handful of items.
    at_ceiling = sum(r["at_ceiling"] for r in rows)
    live = len(rows) - at_ceiling

    NOT_WORTH, MARGINAL = 0.05, 0.10
    hi = hr.get("ci95", [headroom, headroom])[1]
    verdict = ("ENSEMBLE NOT WORTH IT — no judge can beat one model here; route or cascade"
               if headroom < NOT_WORTH else
               "MARGINAL — a judge must be very good to pay for itself"
               if headroom < MARGINAL else
               "WORTH ENSEMBLING — the models disagree usefully")
    # Do not state a verdict the data cannot support: if the threshold lies INSIDE the CI,
    # the two verdicts are not distinguishable at this n. Say so instead of picking one.
    resolved = not (hr.get("ci95") and hr["ci95"][0] < NOT_WORTH < hi)
    if not resolved:
        verdict += (f"  [NOT RESOLVED at n={len(rows)}: the {NOT_WORTH} threshold lies "
                    f"inside the 95% CI, so NOT-WORTH-IT vs MARGINAL is not distinguishable. "
                    f"More queries needed to settle it.]")

    return {
        "headroom": {
            "value": round(headroom, 4),
            "oracle": round(oracle, 4),
            "best_single_model": best_single_model,
            "best_single_score": round(best_single, 4),
            "single_model_scores": {m: round(v, 4) for m, v in singles.items()},
            "verdict": verdict,
            "verdict_resolved": resolved,
            "queries_at_grader_ceiling": at_ceiling,
            "queries_with_live_headroom": live,
            "ceiling_note": (f"{at_ceiling}/{len(rows)} queries have the best candidate "
                             f"already at the grader's maximum, where headroom is 0 BY "
                             f"CONSTRUCTION. The headroom is earned on only {live} queries."),
            "note": "headroom = ORACLE (perfect judge) - BEST SINGLE MODEL. The entire "
                    "value the ensemble+judge pattern can ever buy on this fleet. A real "
                    "judge captures a fraction of it — possibly a negative fraction.",
            **hr,
        },
        "n": len(rows),
        "query_set": SET_NAME,
        "queries_in_set": len(QUERY_BY_ID),
        "missing_candidates": missing,   # in the query set, never generated by the fleet
        "skipped_incomplete": skipped_incomplete,
        "skipped_ungraded": skipped_ungraded,
        "meaningful_spread_threshold": MEANINGFUL,
        "divergent": len(div),
        "degenerate": len(rows) - len(div),
        "all_three_essentially_correct": len(allc),
        "mean_spread": round(statistics.fmean([r["spread"] for r in rows]), 3) if rows else 0,
        "median_spread": round(statistics.median([r["spread"] for r in rows]), 3) if rows else 0,
        # PER-CATEGORY HEADROOM — and, the killer diagnostic, WHO WINS EACH CATEGORY.
        #
        # The obvious objection to a null headroom result is "your query set is skewed —
        # of course the coder won, your queries were code-heavy". This refutes it with
        # data you already have, for $0: compute the best single model WITHIN each
        # category. If the nominal specialist for a category is not the best model ON ITS
        # OWN CATEGORY, the fleet has no specialists — it has one good model and some
        # worse ones, and no query mix will change that.
        #
        # Conclave's fleet (hard set): coder is the best single model in ALL THREE
        # categories. It beats the reasoner at REASONING (0.900 vs 0.807) and beats the
        # general model at GENERAL (0.500 vs 0.440), on a balanced 10/10/10 split. At this
        # scale PARAMETER COUNT BEATS SPECIALIZATION: a 14B model is simply better than a
        # 7B and a 9B, even on their home turf. "Specialist" was a label, not a capability.
        "by_category": {
            c: {
                "n": len(v),
                "divergent": sum(r["divergent"] for r in v),
                "mean_spread": round(statistics.fmean([r["spread"] for r in v]), 3),
                "at_ceiling": sum(r["at_ceiling"] for r in v),
                "single_model_scores": {
                    m: round(statistics.fmean([r["scores"][m] for r in v]), 4) for m in models},
                # THE DIAGNOSTIC: is the category's own specialist the best model on it?
                "best_single": max(
                    models, key=lambda m: statistics.fmean([r["scores"][m] for r in v])),
                "specialist_wins_own_category": max(
                    models, key=lambda m: statistics.fmean([r["scores"][m] for r in v])) == c,
                "oracle": round(statistics.fmean([r["best_score"] for r in v]), 4),
                "headroom": round(
                    statistics.fmean([r["best_score"] for r in v])
                    - max(statistics.fmean([r["scores"][m] for r in v]) for m in models), 4),
                "strict_win_counts": {
                    m: sum(r["strict_winner"] == m for r in v) for m in models},
            }
            for c, v in by_cat.items()},
        # STRICT wins only (a tie is not a win for anybody), plus how often each model was
        # merely tied at the top. The old tie-blind count credited every tie to whichever
        # model came first in EnsembleConfig.candidates — that is how "coder wins 31/36"
        # got published when the honest count is coder 4 / general 4 / reasoning 0.
        "strict_win_counts": {
            m: sum(r["strict_winner"] == m for r in rows) for m in models},
        "tied_at_top_counts": {
            m: sum(r["tied_at_top"] and m in r["best_candidates"] for r in rows)
            for m in models},
        "queries_tied_at_top": sum(r["tied_at_top"] for r in rows),
        "per_query": rows,
    }


def print_report(r: dict) -> None:
    n, d = r["n"], r["divergent"]
    h = r["headroom"]
    miss = r.get("missing_candidates") or []
    if miss:
        # Loud, and BEFORE the numbers — a report over a subset of the query set is not
        # the report you think you are reading.
        print(f"\n!!! {len(miss)}/{r['queries_in_set']} queries in the "
              f"'{r.get('query_set')}' set have NO CANDIDATES and are NOT in the numbers "
              f"below.\n    The fleet never answered them — generating candidates needs a "
              f"GPU boot:\n      CONCLAVE_QUERYSET={r.get('query_set')} CONCLAVE_GW=<ts-ip>:4000 "
              f"python3 orchestrator/candidate_cache.py\n    missing: "
              f"{', '.join(miss[:6])}{' ...' if len(miss) > 6 else ''}")
    print(f"\n=== FLEET HEADROOM — is this candidate set worth ensembling? (n={n}) ===")
    print(f"  single models, used ALONE (no judge, no fan-out):")
    for m, v in sorted(h["single_model_scores"].items(), key=lambda kv: -kv[1]):
        star = "  <- best single" if m == h["best_single_model"] else ""
        print(f"      {m:12s} {v:.3f}{star}")
    if not h.get("best_single_is_significant", True):
        lo, hi2 = h["best_single_margin_ci95"]
        print(f"      !! '{h['best_single_model']}' is NOT significantly better than "
              f"'{h['runner_up']}' (margin CI [{lo:+.3f}, {hi2:+.3f}] includes 0)")
        print(f"         -> the verdict below is hostage to an arbitrary pick.")
    print(f"  ORACLE (a PERFECT judge, always picks the best candidate): {h['oracle']:.3f}")
    ci = (f"  95% CI [{h['ci95'][0]:+.3f}, {h['ci95'][1]:+.3f}]" if h.get("ci95") else "")
    print(f"\n  >>> HEADROOM = oracle - best single = {h['value']:+.4f}{ci}")
    print(f"      (lower bound is vacuous — headroom is >=0 by construction. Judge the UPPER bound.)")
    print(f"      {h['verdict']}")
    print(f"      (This is the ENTIRE value any judge can ever buy here. A real judge")
    print(f"       captures a fraction of it — and a bad one captures a NEGATIVE fraction.)")
    print(f"\n  CEILING: {h['queries_at_grader_ceiling']}/{n} queries have the best candidate "
          f"already at the grader's max,")
    print(f"           where headroom is 0 BY CONSTRUCTION. The headroom is earned on only "
          f"{h['queries_with_live_headroom']} queries.")

    print(f"\n=== who is actually best? (TIES ARE NOT WINS) ===")
    print(f"  {r['queries_tied_at_top']}/{n} queries are an exact TIE at the top — no model wins them.")
    print(f"  strict (unique) wins:")
    for m, k in sorted(r["strict_win_counts"].items(), key=lambda kv: -kv[1]):
        print(f"      {m:12s} {k:2d}/{n}")
    print(f"  (a tie-blind `max()` would credit ALL ties to whichever model is listed first")
    print(f"   in EnsembleConfig.candidates — that is not a finding, it is a config order.)")

    print(f"\n=== candidate divergence — where a judging task even exists ===")
    print(f"spread = best candidate's grade - worst candidate's grade (0-1 scale)")
    print(f"  mean spread   {r['mean_spread']:.3f}   median {r['median_spread']:.3f}")
    print(f"  DIVERGENT (spread > {r['meaningful_spread_threshold']}): {d}/{n}"
          f"  ({100*d/n:.0f}%)  <- a real judging task exists")
    print(f"  DEGENERATE (candidates interchangeable): {r['degenerate']}/{n}"
          f"  ({100*r['degenerate']/n:.0f}%)  <- nothing to judge")
    print(f"  all three specialists essentially correct: {r['all_three_essentially_correct']}/{n}")
    print("\n=== per-category — DOES THE SPECIALIST EVEN WIN ITS OWN CATEGORY? ===")
    print("  (if not, the fleet has no specialists: it has one good model and some worse")
    print("   ones — and NO query mix can fix that. This refutes 'your query set is skewed'.)")
    any_usurped = False
    for c, v in r["by_category"].items():
        bs = v.get("best_single", "?")
        own = v.get("specialist_wins_own_category")
        flag = "" if own else f"  <<< '{bs}' BEATS THE '{c}' SPECIALIST ON ITS OWN TURF"
        if not own:
            any_usurped = True
        print(f"\n  {c.upper():10s} n={v['n']}  at-ceiling {v.get('at_ceiling', 0)}/{v['n']}"
              f"  divergent {v['divergent']}/{v['n']}  headroom {v.get('headroom', 0):+.4f}")
        for m, s in sorted(v.get("single_model_scores", {}).items(), key=lambda kv: -kv[1]):
            mark = " <- best" if m == bs else ""
            print(f"      {m:10s} {s:.3f}  strict wins "
                  f"{v.get('strict_win_counts', {}).get(m, 0)}/{v['n']}{mark}")
        print(f"      ORACLE     {v.get('oracle', 0):.3f}{flag}")
    if any_usurped:
        print(f"\n  !! At least one specialist is NOT the best model on its OWN category.")
        print(f"     PARAMETER COUNT IS BEATING SPECIALIZATION. This fleet has no specialists,")
        print(f"     and no reshuffling of the query mix will produce headroom that is not there.")
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
    assert a["divergent"] and a["spread"] == 0.8 and a["strict_winner"] == "coder"
    assert not b["divergent"], "candidates within 0.05 are interchangeable — no judging task"
    assert r["divergent"] == 1 and r["degenerate"] == 1

    h = r["headroom"]
    assert h["best_single_model"] == "coder", h
    assert abs(h["value"]) < 1e-9, f"a fleet whose best model IS the oracle has ZERO headroom: {h}"
    assert "NOT WORTH IT" in h["verdict"], h["verdict"]

    # ---- THE TIE BUG. max() takes the FIRST max, so a tie-blind argmax credits every tie
    # to whichever model is listed first in the config. That is how "coder is best on 31/36"
    # got published when the honest count was coder 4 / general 4. Ties must be NOBODY's win,
    # and the counts must be invariant to candidate ORDER.
    tied = {qs[2]["id"]: [{"model": m, "content": "tie", "error": None}
                          for m in ("coder", "reasoning", "general")]}
    rt = analyse(tied, FakeScorer({"tie": 1.0}))
    row = rt["per_query"][0]
    assert row["tied_at_top"] and row["strict_winner"] is None, "a tie is NOT a win"
    assert sorted(row["best_candidates"]) == ["coder", "general", "reasoning"]
    assert sum(rt["strict_win_counts"].values()) == 0, "nobody wins a tie"

    # ...and the counts must not change when the candidate ORDER changes.
    rev = {qs[2]["id"]: [{"model": m, "content": "tie", "error": None}
                         for m in ("general", "reasoning", "coder")]}
    assert analyse(rev, FakeScorer({"tie": 1.0}))["strict_win_counts"] == rt["strict_win_counts"], \
        "win counts must be invariant to EnsembleConfig.candidates order"

    # ---- denominator drift: a query where a model returned nothing must be DROPPED, not
    # scored — else singles[m] averages a different row set than oracle and headroom collapses.
    holed = {qs[0]["id"]: [{"model": "coder", "content": None, "error": "boom"},
                           {"model": "reasoning", "content": "bad", "error": None},
                           {"model": "general", "content": "mid", "error": None}]}
    rh = analyse(holed, sc)
    assert rh["n"] == 0 and rh["skipped_incomplete"] == [qs[0]["id"]], \
        "an incomplete candidate set must be dropped, not silently re-based"

    # ---- a query in the SET but not in the CACHE was never generated, and must be
    # reported, not silently dropped from n. (analyse iterates the cache, not the set.)
    assert set(r["missing_candidates"]) == {q["id"] for q in QUERY_SET} - set(cache), \
        "queries the fleet never answered must be surfaced, not silently excluded"
    assert r["queries_in_set"] == len(QUERY_SET)

    # The RENDERER, not just the analysis. print_report() referenced a key analyse() had
    # stopped emitting (`best_candidate_counts`, renamed to `strict_win_counts`), so the
    # real path died with a KeyError *before* json.dump — while this demo passed, because
    # it never called print_report. Render into a buffer: any missing key raises here.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_report(r)
        print_report(rt)   # 1 row: no ci95, no runner-up — the sparse-report path
    assert "HEADROOM" in buf.getvalue()

    print("ok — divergence analysis + report renderer verified offline")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
        sys.exit()

    cache = candidate_cache.load()
    if not cache:
        sys.exit(f"no candidate cache for the '{SET_NAME}' query set (and no fixture) — "
                 f"nothing to analyse. Generating candidates needs a GPU boot:\n"
                 f"  CONCLAVE_QUERYSET={SET_NAME} CONCLAVE_GW=<ts-ip>:4000 "
                 f"python3 orchestrator/candidate_cache.py")
    print(f"query set: {SET_NAME} ({len(QUERY_SET)} queries)")
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
