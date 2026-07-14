#!/usr/bin/env python3
"""SELF-MoA — sample the STRONGEST model N times, then synthesize. The escape hatch.

WHY THIS EXISTS. `divergence.py` measures HEADROOM = oracle - best_single, where the oracle
is `max over the fleet's candidates, ONE SAMPLE EACH`. That bounds every policy that SELECTS
among those candidates — a router, a judge in select mode, a majority vote. On conclave's
fleet it is +0.027: nothing to win.

But **the oracle bounds SELECTION, not GENERATION.** Anything that produces candidates that
were not in the set escapes it entirely:

    self-consistency (N samples)   -> enlarges the candidate set
    repeated sampling / tree search-> RAISES THE ORACLE ITSELF
    Mixture-of-Agents (synthesis)  -> the output is not any candidate
    Sakana's Fugu                  -> delegate + verify + synthesize

Self-MoA (arXiv 2502.00674) is the cheapest member of that family, and the literature makes a
sharp, testable prediction about EXACTLY our situation: aggregating N samples from the SINGLE
BEST model beats mixed-model MoA by ~6.6 points, and it wins **specifically in the
hierarchical regime** — where one model dominates. That is conclave's fleet precisely (coder
0.696 vs general 0.527 vs reasoning 0.518; the coder wins all three categories, beating the
reasoner AT reasoning and the general model AT general).

So: drop the two weaker models. Sample the coder N times instead. Synthesize.

WHAT THIS PRODUCES (the four numbers that decide it):

    baseline        the temp-0 coder, one sample          (the number to beat: 0.696)
    mean_sample     the average temp-T sample             (is sampling itself lossy?)
    ORACLE@N        mean over queries of the BEST of N    <- THE HEADLINE
    self_moa        the judge's SYNTHESIS of all N        <- does a real judge capture it?

**ORACLE@N vs ORACLE@3 (=0.722, the whole fleet) is the experiment.** If N samples from one
model raise the ceiling above what three different models could reach, then the fleet was
never the point — the candidate SET SIZE was. And if `self_moa` then beats `baseline`, the
ensemble pattern pays after all, just not the way we built it.

Temperature matters and is not a detail: at temp 0 the N samples collapse to one answer and
the oracle is a lie. We sample at 0.8.

    CONCLAVE_QUERYSET=hard python3 orchestrator/selfmoa.py --generate   # needs the GPU
    CONCLAVE_QUERYSET=hard python3 orchestrator/selfmoa.py --score      # offline, $0
"""
from __future__ import annotations

import itertools
import json
import math
import os
import statistics
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_queryset import active_query_set, active_set_name
from divergence import _t95
from judge_eval import (FROZEN_GRADER_SAMPLES, GradeCache, ReferenceGrader, _grader_from_env,
                        frontier_call)

_HERE = os.path.dirname(os.path.abspath(__file__))

MODEL = "coder"          # the strongest member — Self-MoA's whole premise
N_SAMPLES = 8
TEMPERATURE = 0.8        # temp 0 would collapse the N samples into one; the oracle would lie
PORT = int(os.environ.get("SELFMOA_PORT", "18011"))


def path(name: str, s: str | None = None) -> str:
    s = s or active_set_name()
    return os.path.join(_HERE, f"{name}_{s}.json")


def fixture(name: str, s: str | None = None) -> str:
    s = s or active_set_name()
    return os.path.join(_HERE, "eval_fixtures", f"{name}_{s}.json")


def _load(p: str, fx: str) -> dict:
    for cand in (p, fx):
        if os.path.exists(cand):
            with open(cand) as f:
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


# ---------------------------------------------------------------- generation (needs GPU)
def sample_once(prompt: str, temperature: float) -> str:
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature, "max_tokens": 1024}
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def generate() -> dict:
    """N samples per query from ONE model. Resumes: a query already at N samples is skipped."""
    qs = active_query_set()
    out = path("eval_selfmoa_samples")
    cache = _load(out, "/nonexistent")   # never resume from the fixture — that is not OUR run
    for i, q in enumerate(qs, 1):
        have = cache.get(q["id"], [])
        if len(have) >= N_SAMPLES:
            continue
        for k in range(len(have), N_SAMPLES):
            try:
                have.append(sample_once(q["prompt"], TEMPERATURE))
            except Exception as e:
                print(f"  {q['id']} sample {k}: FAILED {e}", file=sys.stderr)
        cache[q["id"]] = have
        with open(out, "w") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        print(f"  [{i}/{len(qs)}] {q['id']}: {len(have)} samples")
    return cache


# ---------------------------------------------------------------- scoring (offline, $0)
def _oracle_at_k(scores: list[float], k: int) -> float:
    """Expected best-of-k when k candidates are drawn from this query's samples. Exact — the
    mean of max() over all C(n,k) subsets (n<=8, so 56 subsets at worst)."""
    return statistics.fmean([max(c) for c in itertools.combinations(scores, k)])


def score(cache: dict, scorer, baseline: dict, fleet_oracle_by_q: dict | None = None,
          fleet_k: int = 0) -> dict:
    """baseline = {qid: temp-0 single-sample score for MODEL} from the divergence run.
    fleet_oracle_by_q = {qid: max over the FLEET's candidates} — for the matched-k paired CI.
    fleet_k = how many candidates the fleet's oracle maxes over (its model count)."""
    qs = {q["id"]: q for q in active_query_set()}
    rows = []
    for qid, samples in cache.items():
        q = qs.get(qid)
        if not q or not samples:
            continue
        scores = [scorer.score(q, s) for s in samples]
        scores = [s for s in scores if s is not None]   # None = grader gave no verdict; NOT 0
        if not scores:
            continue
        rows.append({
            "id": qid, "category": q["category"], "n_samples": len(scores),
            "scores": [round(s, 4) for s in scores],
            "oracle": max(scores),          # best of N — the ceiling GENERATION reaches
            "mean": statistics.fmean(scores),
            "worst": min(scores),
            "baseline": baseline.get(qid),  # the temp-0 single sample we already measured
        })

    if not rows:
        return {"n": 0}

    oracle_n = statistics.fmean([r["oracle"] for r in rows])
    mean_sample = statistics.fmean([r["mean"] for r in rows])
    have_base = [r for r in rows if r["baseline"] is not None]
    base = statistics.fmean([r["baseline"] for r in have_base]) if have_base else None

    # RECORD HOW THE ORACLE WAS GRADED. Without this the report mixes two grading configs and
    # says nothing about either: `baseline` is copied from the DIVERGENCE run (samples=3), while
    # `oracle_at_n` is graded HERE at whatever GRADER_SAMPLES happened to be set to — it was 1 in
    # the run that produced the retracted +0.058. An oracle is a MAX, so noisy single grades
    # INFLATE it while the mean-of-3 baseline is variance-reduced: the gap was manufactured by
    # the grader config. Nothing in the file recorded that, so nobody could see it. Downstream
    # (selfmoa_judge) now refuses to quote an oracle-relative number unless this block matches
    # its own grader — which it can only do if the block exists.
    out = {
        "n": len(rows), "model": MODEL, "n_samples": N_SAMPLES, "temperature": TEMPERATURE,
        "grader": {"model": getattr(scorer, "model", None),
                   "url": getattr(scorer, "base_url", None),
                   "samples": getattr(scorer, "samples", None)},
        "oracle_at_n": round(oracle_n, 4),
        "mean_sample": round(mean_sample, 4),
        "baseline_temp0": round(base, 4) if base is not None else None,
        "per_query": rows,
    }
    # The headline: does GENERATION raise the ceiling that SELECTION could not?
    if base is not None:
        d = [r["oracle"] - r["baseline"] for r in have_base]
        sem = statistics.stdev(d) / math.sqrt(len(d)) if len(d) > 1 else 0.0
        t = _t95(len(d))
        out["gain_oracle_over_baseline"] = round(statistics.fmean(d), 4)
        out["gain_ci95"] = [round(statistics.fmean(d) - t * sem, 4),
                            round(statistics.fmean(d) + t * sem, 4)]

    # THE ORACLE@k CURVE — because an ORACLE OVER 8 CANDIDATES CANNOT BE COMPARED TO AN
    # ORACLE OVER 3. A max over more draws is higher REGARDLESS of where the draws come from:
    # that is the winner's curse, not a finding about generation. Comparing self-MoA's
    # oracle@8 against the 3-model fleet's oracle@3 confounds the SOURCE of the candidates
    # (one model vs three) with the NUMBER of them (8 vs 3) — and the project has already
    # published that confounded number once (+0.091, of which +0.039 was pure candidate count).
    # So compute the ceiling at EVERY k and let the fleet be compared at ITS k.
    # Exact expectation over all C(n,k) subsets — n<=8, so this is 56 subsets at worst.
    out["oracle_at_k"] = {}
    for k in range(1, N_SAMPLES + 1):
        per_q = [_oracle_at_k(r["scores"], k) for r in rows if len(r["scores"]) >= k]
        if per_q:
            out["oracle_at_k"][str(k)] = round(statistics.fmean(per_q), 4)

    # THE MATCHED-k COMPARISON, WITH AN INTERVAL. A point estimate with no CI is how this
    # project has repeatedly published a number it had to withdraw. Pair per query: self-MoA's
    # best-of-k against the FLEET's best-of-its-own-k on the SAME query, then t-interval the
    # diffs. (z with an estimated sigma at n=30 is anti-conservative — that error is what
    # un-resolved the divergence verdict.)
    if fleet_oracle_by_q and fleet_k:
        paired = [(_oracle_at_k(r["scores"], fleet_k), fleet_oracle_by_q[r["id"]])
                  for r in rows
                  if r["id"] in fleet_oracle_by_q and len(r["scores"]) >= fleet_k]
        if len(paired) > 1:
            dm = [a - b for a, b in paired]
            sem = statistics.stdev(dm) / math.sqrt(len(dm))
            t = _t95(len(dm))
            out["matched_k"] = fleet_k
            out["gain_vs_fleet_matched_k"] = round(statistics.fmean(dm), 4)
            out["gain_vs_fleet_matched_k_ci95"] = [
                round(statistics.fmean(dm) - t * sem, 4),
                round(statistics.fmean(dm) + t * sem, 4)]
            out["gain_vs_fleet_matched_k_n"] = len(dm)
    return out


def report(r: dict, fleet_oracle: float | None = None, fleet_n: int = 3) -> None:
    print(f"\n=== SELF-MoA — N={r['n_samples']} samples of '{r['model']}' @ temp "
          f"{r['temperature']} (n={r['n']} queries) ===")
    print(f"  baseline  — the temp-0 coder, ONE sample        {r['baseline_temp0']:.3f}")
    print(f"  mean      — an average temp-{r['temperature']} sample          "
          f"{r['mean_sample']:.3f}")
    print(f"  ORACLE@{r['n_samples']}  — the BEST of {r['n_samples']} samples              "
          f"{r['oracle_at_n']:.3f}")
    if "gain_oracle_over_baseline" in r:
        lo, hi = r["gain_ci95"]
        print(f"\n  >>> ORACLE@{r['n_samples']} - baseline = "
              f"{r['gain_oracle_over_baseline']:+.4f}  95% CI [{lo:+.3f}, {hi:+.3f}]")
    curve = r.get("oracle_at_k") or {}
    if curve:
        print("\n  ORACLE@k — the ceiling as a function of CANDIDATE COUNT:")
        print("    " + " · ".join(f"@{k} {v:.3f}" for k, v in curve.items()))
        print("    This curve is a WINNER'S CURSE: a max over more draws rises on its own.")
        print("    Any oracle-vs-oracle comparison must therefore be made at MATCHED k.")

    if fleet_oracle is not None:
        # MATCHED-k COMPARISON, and ONLY that. The fleet's oracle is a max over its
        # fleet_n candidates (3 models x 1 sample). Comparing it against a max over 8
        # measures "8 lottery tickets beat 3", which is true of any candidate source and
        # says nothing about whether SAMPLING ONE MODEL beats RUNNING THREE. The honest
        # question is: at the SAME candidate budget, does one model sampled k times reach a
        # higher ceiling than k different models? Answer that; refuse to print the other.
        k_matched = str(fleet_n)
        print(f"\n  vs THE WHOLE FLEET's oracle ({fleet_n} models, 1 sample each): "
              f"{fleet_oracle:.3f}")
        if k_matched in curve:
            dm = curve[k_matched] - fleet_oracle
            ci = r.get("gain_vs_fleet_matched_k_ci95")
            ci_s = f"  95% CI [{ci[0]:+.3f}, {ci[1]:+.3f}]" if ci else "  (no CI)"
            print(f"  >>> AT MATCHED CANDIDATE COUNT (k={fleet_n}): self-MoA "
                  f"{curve[k_matched]:.3f} vs fleet {fleet_oracle:.3f} = {dm:+.4f}{ci_s}")
            if dm > 0:
                print(f"      Sampling ONE model {fleet_n}x reaches a HIGHER ceiling than "
                      f"running {fleet_n} DIFFERENT models.")
                print(f"      The fleet was never the point — and this is not just the "
                      f"candidate count, because")
                print(f"      the count is held fixed here.")
            else:
                print(f"      At equal candidate budget the FLEET's ceiling is higher — the "
                      f"decorrelation is real.")
        if curve:
            d_raw = r["oracle_at_n"] - fleet_oracle
            print(f"\n  (For the record, the UNMATCHED figure is {d_raw:+.4f} — oracle@"
                  f"{r['n_samples']} vs oracle@{fleet_n}. It is CONFOUNDED BY CANDIDATE COUNT")
            print(f"   and must not be quoted: of it, "
                  f"{r['oracle_at_n'] - curve.get(k_matched, r['oracle_at_n']):+.4f} is bought "
                  f"by nothing but extra draws.)")


def demo() -> None:
    """Offline self-check — no network, no GPU.

    Uses the ACTIVE query set, not a hard-coded one: score() resolves queries via
    active_query_set(), so pinning the demo to "hard" made it pass only when
    CONCLAVE_QUERYSET=hard happened to be exported, and fail otherwise. A self-check that
    depends on an env var you forgot to set is not a self-check."""
    qs = active_query_set()[:2]

    class Fake:
        name = "fake"
        def __init__(self, t): self.t = t
        def score(self, q, a): return self.t[a]

    cache = {qs[0]["id"]: ["good", "bad", "mid"], qs[1]["id"]: ["mid", "mid", "good"]}
    sc = Fake({"good": 1.0, "mid": 0.5, "bad": 0.0})
    base = {qs[0]["id"]: 0.5, qs[1]["id"]: 0.5}
    r = score(cache, sc, base)
    assert r["n"] == 2
    # oracle = max per query: 1.0 and 1.0 -> 1.0.  mean = 0.5 and 0.667.
    assert abs(r["oracle_at_n"] - 1.0) < 1e-6, r
    assert abs(r["baseline_temp0"] - 0.5) < 1e-6, r
    assert abs(r["gain_oracle_over_baseline"] - 0.5) < 1e-6, r

    # A grader that returns None (no usable verdict) must be DROPPED, never scored 0.0 —
    # a zero would silently drag the oracle down and understate the mechanism we are testing.
    class Nones:
        name = "n"
        def score(self, q, a): return None if a == "bad" else 1.0
    r2 = score({qs[0]["id"]: ["good", "bad"]}, Nones(), {})
    assert r2["per_query"][0]["n_samples"] == 1, "ungraded samples must be dropped, not zeroed"
    print("ok — self-moa scoring verified offline (oracle@N, baseline delta, None-drop)")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo(); sys.exit()

    if "--generate" in sys.argv:
        print(f"sampling '{MODEL}' {N_SAMPLES}x @ temp {TEMPERATURE} on :{PORT} ...")
        c = generate()
        print(f"done — {len(c)} queries -> {path('eval_selfmoa_samples')}")
        sys.exit()

    # --score (default): offline
    cache = _load(path("eval_selfmoa_samples"), fixture("eval_selfmoa_samples"))
    if not cache:
        sys.exit("no samples — run --generate against a live coder first (needs a GPU)")

    div = _load(os.path.join(_HERE, f"eval_divergence_{active_set_name()}.json"),
                os.path.join(_HERE, "eval_fixtures",
                             f"eval_divergence_{active_set_name()}.json"))
    baseline = {r["id"]: r["scores"].get(MODEL) for r in div.get("per_query", [])}
    fleet_oracle = div.get("headroom", {}).get("oracle")
    # The fleet's candidate count, from the data — NOT a hardcoded 3. The whole point of the
    # matched-k comparison is that k is the confound; reading it from the report means a
    # 5-model fleet gets compared at k=5 without anyone remembering to change a constant.
    fleet_n = len(div.get("headroom", {}).get("single_model_scores") or {}) or 3
    # Per-query fleet oracle = max over the fleet's candidates on that query. Pairs against
    # self-MoA's best-of-fleet_n on the SAME query, which is what makes a paired CI legitimate.
    fleet_oracle_by_q = {r["id"]: max(r["scores"].values())
                         for r in div.get("per_query", []) if r.get("scores")}

    base_url, model, key = _grader_from_env()
    n = int(os.environ.get("GRADER_SAMPLES", str(FROZEN_GRADER_SAMPLES)))
    _assert_grader_matches_baseline(div, base_url, model, n)
    gc = GradeCache()
    scorer = ReferenceGrader(base_url, model, key, call=frontier_call, samples=n, cache=gc)
    print(f"grading {sum(len(v) for v in cache.values())} samples with {model} ...")
    r = score(cache, scorer, baseline, fleet_oracle_by_q, fleet_n)
    report(r, fleet_oracle, fleet_n)
    print(f"\ngrader calls: {gc.misses} live (paid), {gc.hits} from cache")
    out = path("eval_selfmoa_report")
    with open(out, "w") as f:
        json.dump(r, f, indent=2)
    print(f"saved -> {out}")
