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
from divergence import _t95
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


def _assert_grader_matches_baseline(div: dict, base_url: str, model: str, samples: int) -> None:
    """THE DEFECT THAT KILLED THE HEADLINE. The baseline comes from the divergence run; the
    self-MoA arms are graded HERE. If the two are graded under different grader configs, they
    are NOT COMPARABLE — and the difference does not crash, it just silently biases the result.

    It happened: to save money the self-MoA grading ran at GRADER_SAMPLES=1 while the baseline
    had been graded at GRADER_SAMPLES=3. A mean-of-3 is variance-reduced and sits ~0.011 BELOW
    its own single-grade counterpart on the identical answers, so the baseline was depressed by
    construction while the oracle (a MAX) was inflated by full-noise single grades. On a
    matched baseline the published gain (+0.058, CI [+0.005, +0.110]) becomes +0.047 with a CI
    that CROSSES ZERO. The result was an artifact of the grading config, not a finding.

    Refuse to produce a number rather than produce a wrong one."""
    g = div.get("grader") or {}
    want = (g.get("model"), g.get("url"), g.get("samples"))
    have = (model, base_url, samples)
    if any(v is None for v in want):
        print(f"WARNING: the divergence report records no grader config, so the baseline's "
              f"grading CANNOT be verified against this run's ({have}). Treat any comparison "
              f"against `baseline` as UNVALIDATED.", file=sys.stderr)
        return
    if want != have:
        sys.exit(
            f"\nFATAL — BASELINE AND THIS RUN ARE NOT GRADED THE SAME WAY. Refusing to emit a\n"
            f"number that would be an artifact of the grader config rather than a finding.\n\n"
            f"  baseline graded under : model={want[0]} url={want[1]} samples={want[2]}\n"
            f"  this run grades under : model={have[0]} url={have[1]} samples={have[2]}\n\n"
            f"A mean-of-N grade is variance-reduced and sits BELOW its own single-grade\n"
            f"counterpart on identical answers, while an ORACLE (a max) is INFLATED by noisy\n"
            f"single grades. Comparing across configs biases the result in the ensemble's\n"
            f"favour. Set GRADER_SAMPLES={want[2]} (and the same model/url), or re-grade the\n"
            f"baseline at this run's config.\n")


def _oracle_is_comparable(rep: dict, g_model: str, g_base: str, samples: int) -> bool:
    """Was the ORACLE graded exactly the way THIS run grades?

    An oracle is a MAX over samples, so noisy single grades INFLATE it, while the judge and the
    baseline (means) are variance-reduced. Comparing across grader configs manufactures a gap.
    That is not hypothetical: it is how the retracted +0.058 was produced. So the oracle-relative
    statistic is only emitted when the configs match EXACTLY — and a report that records no
    grader at all (the frozen one does not) is NOT a match. Unknown is not matched."""
    g = rep.get("grader") or {}
    return (g.get("model"), g.get("url"), g.get("samples")) == (g_model, g_base, samples)


def _demo() -> None:
    ok = {"grader": {"model": "claude-sonnet-5", "url": "https://api.anthropic.com", "samples": 3}}
    assert _oracle_is_comparable(ok, "claude-sonnet-5", "https://api.anthropic.com", 3)
    # the EXACT defect that voided +0.058: oracle graded at 1, this run at 3
    assert not _oracle_is_comparable(
        {"grader": {"model": "claude-sonnet-5", "url": "https://api.anthropic.com", "samples": 1}},
        "claude-sonnet-5", "https://api.anthropic.com", 3), "samples mismatch must NOT compare"
    # a different grader model is not comparable either
    assert not _oracle_is_comparable(ok, "gpt-5.2-2025-12-11", "https://api.openai.com", 3)
    # UNKNOWN IS NOT UNBIASED — the frozen report records no grader; it must not sail through
    assert not _oracle_is_comparable({}, "claude-sonnet-5", "https://api.anthropic.com", 3)
    assert not _oracle_is_comparable({"grader": None}, "claude-sonnet-5",
                                     "https://api.anthropic.com", 3)
    print("ok — oracle comparability guard verified offline")


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
    # SOLO — the ABLATION. Same judge, same question, NO CANDIDATES.
    #
    # This is the control that makes a synthesize number interpretable, and without it the
    # headline claim is unfalsifiable. In synthesize mode the judge is TOLD to write its own
    # answer, so `chosen == -1` on every query and nothing in the output reveals whether it
    # USED the candidates or ignored them and answered from its own knowledge. The two are
    # observationally identical.
    #
    # Solo separates them:
    #   synthesize >> solo  -> the candidates carried information. Aggregation worked.
    #   synthesize ~= solo  -> the judge ignored them. The "synthesis" is just the judge
    #                          answering the question, and the ensemble contributed NOTHING.
    # A weak judge scoring near the strong candidates is only impressive if it could NOT have
    # done it alone.
    if mode == "solo":
        msgs = [{"role": "user", "content": query["prompt"]}]   # Gemma has no system role
        raw = call(base, model, msgs, 180.0, response_format=None, api_key=key, temperature=0)
        return {"answer": raw, "chosen": -1, "truncated": False}

    original = samples                       # keep the FULL texts — see below
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
    # Grade the ORIGINAL sample, never the truncated one. Truncation limits what the judge
    # could READ; it is not a property of the answer it CHOSE. Grading the chopped string
    # scores the judge for an answer no model ever produced — which is precisely the
    # "the truncated judge loses for the wrong reason" trap this file warns about above.
    if mode == "select" and isinstance(chosen, int) and 0 <= chosen < len(original):
        ans = original[chosen]
    elif (not ans) and isinstance(chosen, int) and 0 <= chosen < len(original):
        ans = original[chosen]
    return {"answer": ans, "chosen": chosen, "truncated": truncated}


if __name__ == "__main__":
    if "--demo" in sys.argv:
        _demo()
        sys.exit(0)

    mode = "synthesize"
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]
    assert mode in ("select", "synthesize", "solo"), mode

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

    # BEFORE A SINGLE PAID CALL — both config errors that already produced a published-and-
    # retracted number. Fail fast, not after 300 API calls and a plausible-looking result.
    #
    # (1) JUDGE == GRADER. The same model chooses the answer and then grades it: in select mode
    #     it picks what it will score highly, in synthesize mode it marks its own homework. The
    #     original guard computed this and then only ever READ it inside `if ignored_candidates`,
    #     so a judge that selected properly while BEING the grader sailed through. That is
    #     exactly how the published +0.058 was produced — the run exported only GRADER_*, and
    #     JUDGE_* silently fell back to it. ALLOW_JUDGE_IS_GRADER=1 to override (it will still
    #     be marked VOID in the report).
    if (model.strip().lower() == g_model.strip().lower()
            and os.environ.get("ALLOW_JUDGE_IS_GRADER") != "1"):
        sys.exit(
            f"\nFATAL — THE JUDGE IS THE GRADER ({model}).\n"
            f"  The same model would choose the answer and then grade it. In select mode it\n"
            f"  picks what it will score highly; in synthesize mode it marks its own homework.\n"
            f"  This project has already published and retracted a number produced this way.\n\n"
            f"  Set JUDGE_MODEL/JUDGE_URL/JUDGE_API_KEY to a DIFFERENT model than GRADER_* —\n"
            f"  ideally a WEAKER one, so it has to READ the candidates rather than out-answer\n"
            f"  them. e.g. the in-fleet judge:\n"
            f"    JUDGE_URL=http://localhost:18003 JUDGE_MODEL=general JUDGE_API_KEY=none\n")

    # (2) The baseline was graded by the divergence run. If this run grades under a different
    #     config, the comparison is an artifact of the grader, not a finding.
    _n_grader = int(os.environ.get("GRADER_SAMPLES", str(FROZEN_GRADER_SAMPLES)))
    _assert_grader_matches_baseline(_load("eval_divergence"), g_base, g_model, _n_grader)

    out_p = _p(f"eval_selfmoa_judged_{mode}_{model.replace('/', '_')}")
    # Fall back to the committed fixture, so a FRESH CLONE (which has only eval_fixtures/)
    # replays the judge's answers for $0 instead of trying to re-judge against a GPU that is
    # not there. Without this, every OTHER arm replays offline but this one silently needs a
    # live judge — the exact reproducibility gap the rest of the harness closes.
    judged = _load(f"eval_selfmoa_judged_{mode}_{model.replace('/', '_')}")

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
    t = _t95(len(d))
    lo, hi = statistics.fmean(d) - t * sem, statistics.fmean(d) + t * sem

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

    # MODE-AWARE. `chosen == -1` means OPPOSITE things in the two modes:
    #   select    — the judge was told to pick an index. -1 means it IGNORED the task. Bad.
    #   synthesize— the judge was TOLD to emit {"chosen": -1, "answer": "<its own>"}. -1 is the
    #               INSTRUCTED output. Voiding on it made synthesize mode UNRUNNABLE: wrote_own
    #               is 30/30 BY CONSTRUCTION, so every synthesis run voided itself.
    #
    # And in synthesize mode `chosen` CANNOT distinguish the thing we actually fear — a judge
    # that ignored the candidates and answered from its own knowledge looks EXACTLY like one
    # that synthesized them. Both emit -1. The discriminating evidence is not in this field at
    # all; it is the SOLO ABLATION (--mode solo): the same judge, same query, NO candidates.
    # Run `--mode solo` as a separate arm and compare its score to synthesize by hand:
    # synthesize ~= solo means the candidates contributed nothing.
    ignored_candidates = (mode == "select"
                          and len(rows) > 0 and wrote_own / len(rows) > 0.5)

    # THE BUG THAT LET THE HEADLINE THROUGH. `judge_is_grader` was computed and then only ever
    # READ INSIDE `if ignored_candidates:` — so a judge that SELECTED properly (chosen != -1 on
    # every query) while being the SAME MODEL as the grader sailed through with no warning. That
    # is exactly how the published select-mode number was produced: the run exported only
    # GRADER_*, JUDGE_* fell back to the grader, and claude-sonnet-5 graded the answer that
    # claude-sonnet-5 had chosen. It picks what it will score highly. VOID.
    void = ignored_candidates or judge_is_grader

    # A select-mode judge picks one of the candidates, so it CANNOT beat the oracle (the max
    # over those same candidates). A score above it is arithmetically impossible and means the
    # judge did not really select — it wrote something, or the arms are graded differently.
    if mode == "select" and oracle is not None and judge_score > oracle + 1e-6:
        void = True
        print(f"\n!!!!!! IMPOSSIBLE — select-mode judge ({judge_score:.3f}) EXCEEDS the oracle "
              f"({oracle:.3f}).\n  A judge that picks one of N candidates cannot beat the best "
              f"of those N.\n  Either it did not actually select, or the two arms are not "
              f"graded the same way.")

    if judge_is_grader:
        print(f"\n!!!!!! RESULT VOID — THE JUDGE IS THE GRADER ({model}) !!!!!!")
        print(f"  The same model chose the answer and then graded it. In select mode it picks")
        print(f"  what it will score highly; in synthesize mode it marks its own homework.")
        print(f"  Point JUDGE_* at a different model than GRADER_* — ideally a WEAKER one, so it")
        print(f"  has to read the candidates rather than out-answer them.")

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

    # THE ORACLE IS ONLY COMPARABLE IF IT WAS GRADED THE WAY THIS RUN IS GRADED.
    # An ORACLE is a MAX over samples, so noisy single grades INFLATE it, while the judge and
    # the baseline (means) are variance-reduced. The frozen selfmoa report records NO grader
    # config at all and its arms were graded at samples=1 — so "captured X% of the oracle gap"
    # is a ratio between two differently-graded quantities. The gain vs baseline is matched and
    # sound; this ratio is not. Refuse to state it rather than emit an unmatched number.
    oracle_grader = (rep.get("grader") or {})
    oracle_matched = _oracle_is_comparable(rep, g_model, g_base, n)

    print(f"\n=== SELF-MoA JUDGE ({mode}) — n={len(rows)} ===")
    print(f"  baseline  (temp-0 coder, no judge)   {baseline:.3f}")
    print(f"  JUDGE     ({mode} over 8 samples)    {judge_score:.3f}")
    if oracle_matched:
        print(f"  ORACLE@8  (a PERFECT selector)       {oracle:.3f}")
    else:
        print(f"  ORACLE@8  (a PERFECT selector)       {oracle:.3f}  <- ⚠ UNMATCHED GRADING")
    print(f"\n  >>> judge - baseline = {statistics.fmean(d):+.4f}  95% CI [{lo:+.3f}, {hi:+.3f}]")
    gap = oracle - baseline
    if gap > 0 and oracle_matched:
        frac = (judge_score - baseline) / gap
        print(f"  >>> captured {100*frac:.0f}% of the {gap:+.3f} oracle gap")
    elif gap > 0:
        print(f"  >>> ORACLE COMPARISON SUPPRESSED — the oracle was NOT graded like this run")
        print(f"      (oracle: {oracle_grader or 'no grader recorded'} vs this run: "
              f"model={g_model} samples={n}).")
        print(f"      An oracle is a MAX, so noisy grades inflate it. '% of gap captured' would")
        print(f"      be a ratio of two differently-graded numbers. Re-grade the oracle at")
        print(f"      samples={n} to restore it. The gain vs baseline above IS matched and sound.")
    print(f"  (judge wrote its own answer on {wrote_own}/{len(rows)} queries)")
    if void:
        print(f"\n  ** VOID — see the warning(s) above. This number must NOT be quoted. **")
    elif lo > 0:
        print(f"\n  ** THE JUDGE BEATS THE BASELINE. Generation + selection PAYS. **")
    else:
        print(f"\n  ** No significant gain over just calling the model once. **")
        print(f"     The ceiling is real ({oracle:.3f}) but nothing can reach it —")
        print(f"     the bottleneck is the SELECTOR, not the candidates.")

    tag = f"{mode}_{model.replace(chr(47), chr(95))}"
    json.dump({"mode": mode, "judge_model": model, "grader_model": g_model,
               # Record the grader config in the SHAPE THE GUARD READS
               # (_assert_grader_matches_baseline -> div["grader"]["model"/"url"/"samples"]).
               # Writing only the flat keys is how the provenance hole opened: the frozen
               # eval_selfmoa_report_hard.json records no grader at all, which is precisely
               # why nobody caught that its arms were graded at samples=1 while the baseline
               # was at samples=3. A number whose grading cannot be verified is a number that
               # gets retracted.
               "grader": {"model": g_model, "url": g_base, "samples": n},
               "judge_url": base,          # NB: "judge" below is the SCORE, not the config
               "VOID": bool(void),
               "void_reason": ("; ".join(filter(None, [
                   f"judge IS the grader ({model}) — it graded the answer it chose"
                       if judge_is_grader else "",
                   f"judge ignored the candidates and wrote its own answer on "
                   f"{wrote_own}/{len(rows)} queries" if ignored_candidates else "",
               ])) or None),
               "n_truncated": n_trunc,
               "max_candidate_tokens": MAX_CANDIDATE_TOKENS,
               "grader_samples": n,
               "n": len(rows), "judge": round(judge_score, 4),
               "baseline": baseline, "oracle_at_n": oracle,
               "oracle_grading_matched": bool(oracle_matched),
               "gain": round(statistics.fmean(d), 4), "gain_ci95": [round(lo, 4), round(hi, 4)],
               "wrote_own_answer": wrote_own, "per_query": rows},
              open(_p(f"eval_selfmoa_judge_{tag}"), "w"), indent=2)
    print(f"\ngrader calls: {gc.misses} live, {gc.hits} cached")
    print(f"saved -> {_p(f'eval_selfmoa_judge_{tag}')}")
