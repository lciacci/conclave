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

import json
import math
import os
import statistics
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from eval_queryset import active_query_set, active_set_name
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
def score(cache: dict, scorer, baseline: dict) -> dict:
    """baseline = {qid: temp-0 single-sample score for MODEL} from the divergence run."""
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

    out = {
        "n": len(rows), "model": MODEL, "n_samples": N_SAMPLES, "temperature": TEMPERATURE,
        "oracle_at_n": round(oracle_n, 4),
        "mean_sample": round(mean_sample, 4),
        "baseline_temp0": round(base, 4) if base is not None else None,
        "per_query": rows,
    }
    # The headline: does GENERATION raise the ceiling that SELECTION could not?
    if base is not None:
        d = [r["oracle"] - r["baseline"] for r in have_base]
        sem = statistics.stdev(d) / math.sqrt(len(d)) if len(d) > 1 else 0.0
        out["gain_oracle_over_baseline"] = round(statistics.fmean(d), 4)
        out["gain_ci95"] = [round(statistics.fmean(d) - 1.96 * sem, 4),
                            round(statistics.fmean(d) + 1.96 * sem, 4)]
    return out


def report(r: dict, fleet_oracle: float | None = None) -> None:
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
    if fleet_oracle is not None:
        d = r["oracle_at_n"] - fleet_oracle
        print(f"\n  vs THE WHOLE FLEET's oracle (3 models, 1 sample each): {fleet_oracle:.3f}")
        print(f"  >>> sampling ONE model {r['n_samples']}x beats the 3-model oracle by "
              f"{d:+.4f}")
        if d > 0:
            print(f"      GENERATION ESCAPES THE BOUND that SELECTION could not. The fleet was")
            print(f"      never the point — the CANDIDATE SET SIZE was.")
        else:
            print(f"      even N samples of the best model do NOT beat the 3-model oracle.")


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

    base_url, model, key = _grader_from_env()
    n = int(os.environ.get("GRADER_SAMPLES", str(FROZEN_GRADER_SAMPLES)))
    gc = GradeCache()
    scorer = ReferenceGrader(base_url, model, key, call=frontier_call, samples=n, cache=gc)
    print(f"grading {sum(len(v) for v in cache.values())} samples with {model} ...")
    r = score(cache, scorer, baseline)
    report(r, fleet_oracle)
    print(f"\ngrader calls: {gc.misses} live (paid), {gc.hits} from cache")
    out = path("eval_selfmoa_report")
    with open(out, "w") as f:
        json.dump(r, f, indent=2)
    print(f"saved -> {out}")
