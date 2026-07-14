#!/usr/bin/env python3
"""Can a REAL judge capture the Self-MoA ceiling? (offline — no GPU)

The oracle over 8 samples of the coder is **0.813**, against a 0.696 single-sample
baseline: +0.118 of headroom, 4.4x what the whole 3-model fleet ever offered (+0.027).
GENERATION escapes the bound that SELECTION could not.

But a ceiling is not a result. The fleet's judge captured a NEGATIVE fraction of its
(tiny) ceiling — it scored 0.883 against a 0.933 single model. So the open question is
not "is there a prize" but "can anything actually collect it":

    ORACLE@8      0.813   <- the ceiling. Requires a PERFECT selector.
    baseline      0.696   <- what you get with no judge at all.
    ??? judge     ?????   <- THIS. Everything hinges on it.

Prior art gives a prediction to check ourselves against: a real judge recovers ~21% of
the oracle gap with pointwise scoring and ~61% with pairwise (arXiv 2603.12520). On our
+0.118 that is +0.025 (-> 0.721) to +0.072 (-> 0.768). BOTH beat the 0.696 baseline —
which is the whole difference from the fleet experiment, where 21-61% of +0.027 was
indistinguishable from noise.

Two modes, because they are different mechanisms and the distinction is the entire
subject of this project:
  select     — pick one of the 8 samples. BOUNDED by ORACLE@8 (0.813).
  synthesize — write a new answer from the 8. NOT bounded: it can, in principle,
               exceed the oracle, because its output need not be any candidate.

Runs against the FROZEN samples, so it costs only judge + grader calls. No GPU.

    CONCLAVE_QUERYSET=hard python3 orchestrator/selfmoa_judge.py --mode select
    CONCLAVE_QUERYSET=hard python3 orchestrator/selfmoa_judge.py --mode synthesize
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_queryset import active_query_set, active_set_name
from judge_eval import (FROZEN_GRADER_SAMPLES, GradeCache, ReferenceGrader, _grader_from_env,
                        frontier_call)

_HERE = os.path.dirname(os.path.abspath(__file__))


def _p(name: str) -> str:
    return os.path.join(_HERE, f"{name}_{active_set_name()}.json")


def _load(name: str) -> dict:
    for c in (_p(name), os.path.join(_HERE, "eval_fixtures",
                                     f"{name}_{active_set_name()}.json")):
        if os.path.exists(c):
            with open(c) as f:
                return json.load(f)
    return {}


JUDGE_SYS = (
    "You are a judge over several candidate answers to the same question. The answers "
    "were all produced by the same model at a non-zero temperature, so they differ in "
    "quality, completeness and correctness. Judge substance, not style or length."
)


# A SMALL IN-FLEET JUDGE HAS A SMALL CONTEXT, AND 8 SAMPLES DO NOT ALWAYS FIT.
# Gemma-2's window is 8192 tokens. Measured on the frozen samples: the 8-sample block is a
# median of ~4,600 tokens but a MAX of ~7,695 — and 6 of 30 queries leave under 1,700 tokens
# for the question, the instructions and the judge's own answer. Those overflow.
#
# Silent truncation is the dangerous failure: it starves the judge of candidates, the judge
# scores badly, and you conclude "synthesis doesn't work" when what actually happened is that
# the judge never saw the material. So: truncate DELIBERATELY, to a budget, and REPORT it.
# 6000 is the largest budget that still fits Gemma-2's 8192 window with room for the prompt
# (~300 tok) and for the judge to WRITE its synthesis (~1200 tok): 6000+1500 = 7500 < 8192.
# It truncates 6/30 queries — down from 12/30 at a 5000 budget. Raising it further buys
# nothing (6500 also truncates 6) and 6700 overflows.
#
# FAIRNESS: whatever budget you use, the FRONTIER arm must be re-run at the SAME budget, or
# you are comparing a judge that saw full candidates against one that saw truncated ones —
# and the truncated judge will lose for the wrong reason. The frontier numbers on record
# (select 0.753) were taken WITHOUT truncation, so they are NOT directly comparable to a
# truncated Gemma run. Re-run both, or compare only within a budget.
MAX_CANDIDATE_TOKENS = int(os.environ.get("MAX_CANDIDATE_TOKENS", "6000"))
_CHARS_PER_TOKEN = 4          # rough, and deliberately conservative


def _fit(samples: list[str]) -> tuple[list[str], bool]:
    """Trim samples to a total budget. Returns (samples, was_truncated)."""
    budget = MAX_CANDIDATE_TOKENS * _CHARS_PER_TOKEN
    total = sum(len(s) for s in samples)
    if total <= budget:
        return samples, False
    per = budget // max(1, len(samples))       # equal share — no sample is privileged
    return ([s if len(s) <= per else s[:per] + "\n...[truncated]" for s in samples], True)


def judge_once(query: dict, samples: list[str], mode: str, call, base, model, key) -> dict:
    samples, truncated = _fit(samples)
    blocks = "\n\n".join(f"[Answer {i}]\n{s}" for i, s in enumerate(samples))
    if mode == "select":
        task = ('Pick the single BEST answer. Respond ONLY with JSON: '
                '{"chosen": <answer index>, "answer": "<verbatim text of that answer>"}')
    else:
        task = ('Write the BEST possible answer to the question, using the candidates as '
                'material. Correct their errors; keep what each gets right. Your answer '
                'need not match any candidate. Respond ONLY with JSON: '
                '{"chosen": -1, "answer": "<your answer>"}')
    msgs = [{"role": "user", "content":
             f"{JUDGE_SYS}\n\nQuestion:\n{query['prompt']}\n\n{blocks}\n\n{task}"}]
    raw = call(base, model, msgs, 180.0, response_format=None, api_key=key, temperature=0)
    try:
        s = raw[raw.index("{"):raw.rindex("}") + 1]
        d = json.loads(s)
        ans, chosen = d.get("answer"), d.get("chosen", -1)
    except Exception:
        ans, chosen = raw, -1     # lenient parse: a judge that ignored the format still answered
    # A judge that "selected" but returned no text: fall back to the sample it named, so a
    # formatting failure is not scored as a WRONG ANSWER.
    if (not ans) and isinstance(chosen, int) and 0 <= chosen < len(samples):
        ans = samples[chosen]
    return {"answer": ans, "chosen": chosen, "truncated": truncated}


if __name__ == "__main__":
    mode = "synthesize"
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]
    assert mode in ("select", "synthesize"), mode

    samples = _load("eval_selfmoa_samples")
    if not samples:
        sys.exit("no samples — run selfmoa.py --generate first")
    qs = {q["id"]: q for q in active_query_set()}

    # GRADER — always. JUDGE — separately pointable, and it MUST be, because the two must
    # not be the same model (see the trap guard below). Defaults to the grader only for
    # backwards compatibility with the frontier-judge run; for a real SYNTHESIS test you
    # want the IN-FLEET judge, which is WEAKER than the candidates and therefore has to
    # actually read them:
    #
    #   JUDGE_URL=http://localhost:4000 JUDGE_MODEL=general JUDGE_API_KEY=none \
    #   GRADER_URL=https://api.anthropic.com GRADER_MODEL=claude-sonnet-5 ... \
    #   CONCLAVE_QUERYSET=hard python3 orchestrator/selfmoa_judge.py --mode synthesize
    #
    # Gemma is GOOGLE and the grader is ANTHROPIC — different houses, so no shared-house
    # bias, and judge != grader so nothing marks its own homework.
    g_base, g_model, g_key = _grader_from_env()
    base = os.environ.get("JUDGE_URL") or g_base
    model = os.environ.get("JUDGE_MODEL") or g_model
    key = os.environ.get("JUDGE_API_KEY") or g_key
    print(f"judge : {model} @ {base}")
    print(f"grader: {g_model} @ {g_base}")

    out_p = _p(f"eval_selfmoa_judged_{mode}_{model.replace('/', '_')}")
    judged = json.load(open(out_p)) if os.path.exists(out_p) else {}

    print(f"judging {len(samples)} queries in '{mode}' mode with {model} ...")
    for i, (qid, ss) in enumerate(samples.items(), 1):
        if qid in judged:
            continue
        try:
            judged[qid] = judge_once(qs[qid], ss, mode, frontier_call, base, model, key)
            json.dump(judged, open(out_p, "w"), indent=2, ensure_ascii=False)
            print(f"  [{i}/{len(samples)}] {qid} chosen={judged[qid]['chosen']}")
        except Exception as e:
            print(f"  [{i}/{len(samples)}] {qid} FAILED: {e}", file=sys.stderr)

    # ---- grade the judge's output on the SAME grader as everything else
    gc = GradeCache()
    n = int(os.environ.get("GRADER_SAMPLES", str(FROZEN_GRADER_SAMPLES)))
    scorer = ReferenceGrader(g_base, g_model, g_key, call=frontier_call, samples=n, cache=gc)
    rows = []
    for qid, j in judged.items():
        if not j.get("answer"):
            continue
        s = scorer.score(qs[qid], j["answer"])
        if s is not None:
            rows.append({"id": qid, "score": s, "chosen": j["chosen"],
                         "truncated": j.get("truncated", False)})

    rep = _load("eval_selfmoa_report")
    oracle = rep.get("oracle_at_n")
    baseline = rep.get("baseline_temp0")
    judge_score = statistics.fmean([r["score"] for r in rows])

    # paired CI vs the baseline — the only comparison that matters
    per_base = {r["id"]: r.get("baseline") for r in rep.get("per_query", [])}
    d = [r["score"] - per_base[r["id"]] for r in rows if per_base.get(r["id"]) is not None]
    sem = statistics.stdev(d) / math.sqrt(len(d)) if len(d) > 1 else 0.0
    lo, hi = statistics.fmean(d) - 1.96 * sem, statistics.fmean(d) + 1.96 * sem

    wrote_own = sum(1 for r in rows if r["chosen"] == -1)
    n_trunc = sum(1 for r in rows if r.get("truncated"))
    if n_trunc:
        print(f"\n  NOTE: candidates were TRUNCATED on {n_trunc}/{len(rows)} queries "
              f"(budget {MAX_CANDIDATE_TOKENS} tok).")
        print(f"        A judge that never saw the material is not evidence that "
              f"synthesis fails.")

    # ------------------------------------------------------------------ THE TRAP GUARD
    # A judge STRONGER than the candidates does not synthesize them — it IGNORES them and
    # writes its own answer. Then, if the grader is the same model as the judge, it marks
    # its own homework and scores ~1.0. That is not a measurement of synthesis; it is
    # "a frontier model answers the question, and then grades itself".
    #
    # This project already retracted exactly this result once (the frontier judge set
    # chosen == -1 on 34/36 of the base run). It happened AGAIN here in synthesize mode:
    # chosen == -1 on 29/30, grader == judge == claude-sonnet-5, score 1.000. Void.
    #
    # Refuse to report it rather than let a 1.000 look like a triumph.
    judge_is_grader = model.strip().lower() == g_model.strip().lower()
    ignored_candidates = len(rows) > 0 and wrote_own / len(rows) > 0.5
    if ignored_candidates:
        print(f"\n!!!!!! RESULT VOID — THE JUDGE IGNORED THE CANDIDATES !!!!!!")
        print(f"  It wrote its OWN answer on {wrote_own}/{len(rows)} queries (chosen == -1).")
        print(f"  It did not synthesize the samples — it just answered the question itself,")
        print(f"  which measures the JUDGE's ability, not the ensemble's.")
        if judge_is_grader:
            print(f"\n  AND THE GRADER IS THE SAME MODEL AS THE JUDGE ({model}).")
            print(f"  It is marking its own homework. A high score here is guaranteed and")
            print(f"  means NOTHING.")
        print(f"\n  A synthesis judge must be NO STRONGER than the candidates, or it has no")
        print(f"  reason to read them. Use the in-fleet judge, and a grader from a DIFFERENT")
        print(f"  house than the judge. Reporting the numbers below FOR THE RECORD ONLY —")
        print(f"  they do not measure synthesis and must not be quoted as if they did.")

    print(f"\n=== SELF-MoA JUDGE ({mode}) — n={len(rows)} ===")
    print(f"  baseline  (temp-0 coder, no judge)   {baseline:.3f}")
    print(f"  JUDGE     ({mode} over 8 samples)    {judge_score:.3f}")
    print(f"  ORACLE@8  (a PERFECT selector)       {oracle:.3f}")
    print(f"\n  >>> judge - baseline = {statistics.fmean(d):+.4f}  95% CI [{lo:+.3f}, {hi:+.3f}]")
    gap = oracle - baseline
    if gap > 0:
        frac = (judge_score - baseline) / gap
        print(f"  >>> captured {100*frac:.0f}% of the {gap:+.3f} oracle gap")
    print(f"  (judge wrote its own answer on {wrote_own}/{len(rows)} queries)")
    if ignored_candidates:
        print(f"\n  ** VOID — see the warning above. This is not a synthesis result. **")
    elif lo > 0:
        print(f"\n  ** THE JUDGE BEATS THE BASELINE. Generation + selection PAYS. **")
    else:
        print(f"\n  ** No significant gain over just calling the model once. **")
        print(f"     The ceiling is real ({oracle:.3f}) but nothing can reach it —")
        print(f"     the bottleneck is the SELECTOR, not the candidates.")

    tag = f"{mode}_{model.replace(chr(47), chr(95))}"
    json.dump({"mode": mode, "judge_model": model, "grader_model": g_model,
               "n": len(rows), "judge": round(judge_score, 4),
               "baseline": baseline, "oracle_at_n": oracle,
               "gain": round(statistics.fmean(d), 4), "gain_ci95": [round(lo, 4), round(hi, 4)],
               "wrote_own_answer": wrote_own, "per_query": rows},
              open(_p(f"eval_selfmoa_judge_{tag}"), "w"), indent=2)
    print(f"\ngrader calls: {gc.misses} live, {gc.hits} cached")
    print(f"saved -> {_p(f'eval_selfmoa_judge_{tag}')}")
