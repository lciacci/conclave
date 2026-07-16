#!/usr/bin/env python3
"""ROUTER PREDICTABILITY — the honest gate before building a router. $0, offline.

The pairwise run (`fleet_pairwise.py`) produced an ORACLE label per query: the model that
actually won it, post-hoc. A real router never sees that — it picks from the QUERY ALONE. This
measures how much of the oracle it can recover from a cheap query-only feature (the query
CATEGORY: code / reasoning / general — the one signal the hard set is literally organized by).

The question (HANDOFF, 2026-07-15): can a query-only picker predict the per-query winner better
than a constant "always call model X"? Two honest traps this guards against:

  1. THE BASELINE. "Always qwen3" is the mean-points winner but a WEAK constant on winner COUNT
     (qwen3 wins on margin, mistral on count). Beating the weak default proves nothing. The bar
     is the BEST constant = argmax per-query wins. Both are reported; the router must beat the best.
  2. IN-SAMPLE OPTIMISM. Picking each category's mode AFTER seeing all labels is optimistic at
     ~10 queries/category. The reported router accuracy is LEAVE-ONE-OUT (mode from the other 29).

Metric-1 scores only DECIDED queries (a pairwise tie has no winner to predict). Metric-2 counts
ties as free (any pick is costless on a tie) — the practical-payoff view. Both reported.

  python3 orchestrator/router_predictability.py            # reads the committed pairwise fixture
"""
import os, sys, json
from collections import Counter

_HERE = os.path.dirname(os.path.abspath(__file__))
FLEET = os.environ.get("MODERN_FLEET", "modern2")


def _load(name):
    for p in (os.path.join(_HERE, f"{name}.json"),
              os.path.join(_HERE, "eval_fixtures", f"{name}.json")):
        if os.path.exists(p):
            return json.load(open(p))
    sys.exit(f"missing {name}.json — run fleet_pairwise.py first")


def category(qid):
    """Query-only feature: the hard-set category prefix (hardcoder / hardreason / hardgen)."""
    return qid.split("-")[0]


def _mode(winners):
    """Most common winner, deterministic tie-break (alphabetical) so results are reproducible."""
    if not winners:
        return None
    c = Counter(winners)
    top = max(c.values())
    return sorted(m for m, n in c.items() if n == top)[0]


def constant_accuracy(pqw, model):
    """Metric-1 (decided-only) and Metric-2 (ties-free) accuracy of 'always pick `model`'."""
    decided = [w for w in pqw.values() if w is not None]
    m1 = sum(1 for w in decided if w == model) / len(decided) if decided else 0.0
    ties = sum(1 for w in pqw.values() if w is None)
    m2 = (sum(1 for w in decided if w == model) + ties) / len(pqw)
    return m1, m2


def router_loo(pqw):
    """Leave-one-out category router: for each query, predict its category's mode winner computed
    from the OTHER queries, then check. Returns (m1_decided, m2_ties_free, per_query_predictions)."""
    ids = list(pqw)
    preds, m1_hits, m1_n, m2_hits = {}, 0, 0, 0
    for qid in ids:
        c = category(qid)
        others = [pqw[o] for o in ids if o != qid and category(o) == c and pqw[o] is not None]
        pred = _mode(others)
        preds[qid] = pred
        actual = pqw[qid]
        if actual is None:            # tie: costless, free win under Metric-2 only
            m2_hits += 1
            continue
        m1_n += 1
        if pred == actual:
            m1_hits += 1
            m2_hits += 1
    m1 = m1_hits / m1_n if m1_n else 0.0
    return m1, m2_hits / len(ids), preds


def router_insample(pqw):
    """Same, but the mode is computed WITH the query included — the optimistic number, for the gap."""
    cat_mode = {}
    cats = sorted({category(q) for q in pqw})   # sorted -> deterministic key order (byte-reproducible fixture)
    for c in cats:
        cat_mode[c] = _mode([pqw[q] for q in pqw if category(q) == c and pqw[q] is not None])
    decided_n = sum(1 for w in pqw.values() if w is not None)
    hits = sum(1 for q, w in pqw.items() if w is not None and cat_mode[category(q)] == w)
    return hits / decided_n if decided_n else 0.0, cat_mode


def main():
    d = _load(f"eval_pairwise_{FLEET}_hard")
    pqw = d["per_query_winner"]
    models = sorted({w for w in pqw.values() if w})
    n = len(pqw)
    decided = sum(1 for w in pqw.values() if w is not None)
    ties = n - decided

    print(f"ROUTER PREDICTABILITY | {FLEET} fleet | {n} queries ({decided} decided, {ties} ties)\n")

    # --- the constant baselines: a router must beat the BEST of these, not the weakest ---
    print("=== constant routers (always call ONE model) ===")
    print(f"  {'model':10s} {'Metric-1':>10s} {'Metric-2':>10s}   (M1=decided-only, M2=ties-free)")
    consts = {}
    for m in models:
        m1, m2 = constant_accuracy(pqw, m)
        consts[m] = m1
        tag = "  <- highest mean pts" if m == "qwen3" else ""
        print(f"  {m:10s} {m1:>9.1%} {m2:>10.1%}{tag}")
    best_const = max(consts, key=consts.get)
    print(f"  -> BEST constant (most wins): {best_const} @ {consts[best_const]:.1%} (Metric-1)")
    print(f"  -> HANDOFF default 'always qwen3': {consts.get('qwen3', 0):.1%} (Metric-1) — the bar to clear\n")

    # --- the query-only router ---
    ins, cat_mode = router_insample(pqw)
    loo_m1, loo_m2, preds = router_loo(pqw)
    print("=== query-only router (feature = query category) ===")
    print(f"  per-category mode winner: {cat_mode}")
    print(f"  in-sample Metric-1:  {ins:>7.1%}   (optimistic — mode picked knowing all labels)")
    print(f"  LEAVE-ONE-OUT M1:    {loo_m1:>7.1%}   <- the honest number")
    print(f"  LEAVE-ONE-OUT M2:    {loo_m2:>7.1%}   (ties-free / practical payoff)\n")

    # --- oracle ceiling + verdict ---
    print("=== verdict ===")
    print(f"  oracle (perfect per-query pick): {decided}/{decided} decided = 100% (by construction)")
    gap_vs_qwen3 = loo_m1 - consts.get("qwen3", 0)
    gap_vs_best = loo_m1 - consts[best_const]
    print(f"  router LOO vs 'always qwen3':  {gap_vs_qwen3:+.1%}")
    print(f"  router LOO vs best constant ({best_const}): {gap_vs_best:+.1%}")
    if gap_vs_best > 0.05:
        print("  -> A query-only router PAYS on quality: it beats the best constant model.")
    elif gap_vs_best > -0.05:
        print(f"  -> A query-only router does NOT beat the best constant ({best_const}). The predictable")
        print("     per-query signal is ~fully captured by 'always call the winningest model'.")
        print("     Router's remaining case is COST/LATENCY (1 call, not 3), not quality.")
    else:
        print(f"  -> The router LOSES to the best constant — category is not a useful routing feature.")
    print(f"\n  CAVEAT: n={decided} decided queries is THIN (~binomial ±10pts). Category is ONE coarse")
    print(f"  feature; a finer query-only signal is untested, but within-category winners look")
    print(f"  near-random (see hardreason), consistent with grader noise on already-strong answers.")

    out = {"fleet": FLEET, "n": n, "decided": decided, "ties": ties,
           "constant_metric1": consts, "best_constant": best_const,
           "category_mode": cat_mode, "router_insample_m1": round(ins, 4),
           "router_loo_m1": round(loo_m1, 4), "router_loo_m2": round(loo_m2, 4),
           "gap_vs_qwen3": round(gap_vs_qwen3, 4), "gap_vs_best_constant": round(gap_vs_best, 4),
           "loo_predictions": preds}
    json.dump(out, open(os.path.join(_HERE, f"eval_router_predictability_{FLEET}_hard.json"), "w"), indent=2)
    print(f"\nsaved -> eval_router_predictability_{FLEET}_hard.json")


def demo():
    """Self-check: a hand-built label set with a KNOWN answer exercises the LOO + constant logic."""
    # cat 'a': winner is always X (perfectly predictable). cat 'b': X,Y,X,Y (near coin-flip).
    pqw = {"a-1": "X", "a-2": "X", "a-3": "X", "a-4": "X",
           "b-1": "X", "b-2": "Y", "b-3": "X", "b-4": "Y", "b-5": None}
    # constants: X wins a(4) + b(2) = 6/8 decided; Y wins 2/8.
    m1_x, _ = constant_accuracy(pqw, "X")
    assert abs(m1_x - 6/8) < 1e-9, m1_x
    # LOO: every 'a' query -> others all X -> predict X -> 4/4 correct.
    #      'b-1'(X): others b = Y,X,Y -> mode Y -> wrong. 'b-2'(Y): others X,X,Y -> X -> wrong.
    #      'b-3'(X): others X,Y,Y -> Y -> wrong. 'b-4'(Y): others X,Y,X -> X -> wrong.
    #      b-5 is a tie (skipped in M1). so LOO M1 = 4/8 decided.
    loo_m1, loo_m2, preds = router_loo(pqw)
    assert abs(loo_m1 - 4/8) < 1e-9, loo_m1
    assert preds["a-1"] == "X" and preds["b-1"] == "Y", preds
    # M2: 4 correct + 1 tie free = 5/9.
    assert abs(loo_m2 - 5/9) < 1e-9, loo_m2
    ins, cat_mode = router_insample(pqw)
    assert cat_mode["a"] == "X", cat_mode
    print("demo OK — LOO, constant, and in-sample logic self-check passed")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        main()
