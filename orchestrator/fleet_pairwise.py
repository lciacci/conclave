#!/usr/bin/env python3
"""PAIRWISE fleet grading — the saturation fix, and the router's real signal.

Absolute reference grading tops out: on the modern fleet 25/30 queries have every strong answer
at 5/5, so it CANNOT tell which model is actually better — 16/30 are exact ties at the top.
Pairwise compares answers TO EACH OTHER (blinded, both orders, position-debiased via the built
PairwiseScorer), which resolves ties two 5/5 answers cannot.

Round-robin the 3 fleet models per query (3 pairs x 2 orders). Then the question that decides
whether a ROUTER has value beyond "always call the strongest model":

  On the queries absolute grading called TIED at the top, does pairwise produce a CONSISTENT
  winner (-> route to that one model, the ensemble/router buys nothing) or do winners SPLIT
  across models (-> genuine per-query variation the absolute grader was blind to, and a router
  can exploit it)?

Offline, $0 GPU. Grader must be NEUTRAL to the fleet lineages (gpt-5.2 = OpenAI, neutral to
Alibaba/Google/Mistral). ~180 grader calls at samples implied by both-order grading.

  GRADER_URL=https://api.openai.com GRADER_MODEL=gpt-5.2-2025-12-11 GRADER_API_KEY=... \
  MODERN_FLEET=modern2 CONCLAVE_QUERYSET=hard python3 orchestrator/fleet_pairwise.py
"""
import os, sys, json, itertools, statistics
from collections import Counter
os.environ.setdefault("CONCLAVE_QUERYSET", "hard")
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from eval_queryset import active_query_set, active_set_name
from judge_eval import GradeCache, PairwiseScorer, _grader_from_env, frontier_call

FLEET = os.environ.get("MODERN_FLEET", "modern2")


def _load(name):
    for p in (os.path.join(_HERE, f"{name}.json"),
              os.path.join(_HERE, "eval_fixtures", f"{name}.json")):
        if os.path.exists(p):
            return json.load(open(p))
    sys.exit(f"missing {name}.json")


def main():
    qset = {q["id"]: q for q in active_query_set()}
    cands = _load(f"eval_candidates_{FLEET}_hard")
    div = _load(f"eval_divergence_{FLEET}_hard")
    # which queries were ABSOLUTE-tied at the top (the saturation cases pairwise must resolve)
    abs_tied = {r["id"] for r in div["per_query"] if r.get("tied_at_top")}

    base, model, key = _grader_from_env()
    # Fail LOUD on an empty key (usually an expired AWS SSO token making `aws ssm` return "")
    # rather than 401-crashing on the first paid call after doing setup work.
    if not key or key in ("none", "dummy"):
        sys.exit(f"GRADER_API_KEY is empty/placeholder ('{key}') — re-auth (aws sso login) and "
                 f"re-export it. Refusing to call {base} with no key.")
    gc = GradeCache()
    ps = PairwiseScorer(base, model, key, call=frontier_call, cache=gc)
    print(f"PAIRWISE {FLEET} fleet | neutral grader {model} @ {base} | {len(cands)} queries")

    models = sorted({c["model"] for v in cands.values() for c in v})
    per_query_winner = {}
    rr_points = {m: 0.0 for m in models}          # round-robin points, 0..2 per query
    for qid, row in cands.items():
        ans = {c["model"]: c["content"] for c in row}
        pts = {m: 0.0 for m in models}
        for a, b in itertools.combinations(models, 2):
            r = ps.score_all(qset[qid], {a: ans[a], b: ans[b]})   # both orders inside
            pts[a] += r[a]; pts[b] += r[b]
        for m in models:
            rr_points[m] += pts[m]
        top = max(pts.values())
        winners = [m for m in models if abs(pts[m] - top) < 1e-9]
        per_query_winner[qid] = winners[0] if len(winners) == 1 else None   # None = pairwise tie

    n = len(cands)
    print("\n=== round-robin standing (mean points/query, max 2.0) ===")
    for m in sorted(models, key=lambda m: -rr_points[m]):
        print(f"  {m:10s} {rr_points[m]/n:.3f}")
    strict = Counter(w for w in per_query_winner.values() if w)
    print(f"\n  clear pairwise winner: {sum(1 for w in per_query_winner.values() if w)}/{n}"
          f"   (pairwise ties: {sum(1 for w in per_query_winner.values() if w is None)})")
    for m in models:
        print(f"    {m:10s} wins {strict.get(m,0)}/{n}")

    # THE ROUTER QUESTION — on the absolute-tied queries, does pairwise CONCENTRATE or SPLIT?
    tie_winners = Counter(per_query_winner[q] for q in abs_tied if per_query_winner.get(q))
    print(f"\n=== on the {len(abs_tied)} ABSOLUTE-TIED queries, who wins the pairwise tie-break? ===")
    for m, c in tie_winners.most_common():
        print(f"    {m:10s} {c}")
    n_tie_resolved = sum(tie_winners.values())
    if tie_winners:
        top_share = tie_winners.most_common(1)[0][1] / max(1, n_tie_resolved)
        if top_share >= 0.75:
            print(f"  -> CONCENTRATED ({top_share:.0%} to one model): the 'ties' hid a consistent")
            print(f"     winner. Route to it; a fan-out router buys little. Confirms route-don't-judge.")
        else:
            print(f"  -> SPLIT (top model only {top_share:.0%}): the absolute grader was BLIND to real")
            print(f"     per-query variation. A router HAS a signal the headroom number could not see.")

    flips = ps.diagnostics["position_flips"]
    print(f"\nposition flips (grader order-sensitive): {len(flips)}/{ps.diagnostics['n_compared']} "
          f"comparisons — {'reliable' if len(flips) < 0.15*max(1,ps.diagnostics['n_compared']) else 'NOISY, treat with caution'}")
    print(f"grader calls: {gc.misses} live (paid), {gc.hits} cached")

    out = {"fleet": FLEET, "grader": {"model": model, "url": base},
           "round_robin_mean": {m: round(rr_points[m]/n, 4) for m in models},
           "per_query_winner": per_query_winner,
           "strict_wins": dict(strict),
           "abs_tied_query_winners": dict(tie_winners),
           "position_flips": flips, "n_compared": ps.diagnostics["n_compared"]}
    json.dump(out, open(os.path.join(_HERE, f"eval_pairwise_{FLEET}_hard.json"), "w"), indent=2)
    print(f"saved -> eval_pairwise_{FLEET}_hard.json")


if __name__ == "__main__":
    main()
