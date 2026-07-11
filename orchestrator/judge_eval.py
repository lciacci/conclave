#!/usr/bin/env python3
"""v3 Chunk 3 — judge eval. Does the in-fleet Gemma judge hold up against a
frontier judge at selecting/synthesizing across the three specialists' answers?

Pipeline (three phases, decoupled by on-disk JSON so the GPU box is up for the
minimum time):

  1. GENERATE  [needs the fleet up]  --generate
     Fan every query out to the specialists (candidate_cache) AND run the in-fleet
     Gemma judge over them. Both need the GPU. Cache candidates + gemma judgments.
  2. FRONTIER  [offline, API only]   --frontier
     Run the frontier judge over the SAME cached candidates. No GPU.
  3. SCORE     [offline]             --score
     Score both judges' final answers and aggregate. No GPU.

DESIGN (see docs/HANDOFF.md, Chunk 3):
  - Scorer is a pluggable protocol. Two impls ship: LocalHeuristic (deterministic,
    offline, for CI/smoke) and ReferenceGrader (an LLM grades each answer 0-5 vs
    the gold reference). Reference-anchored grading self-biases far less than open
    pairwise, so a Claude grader scoring a Claude-judged answer is acceptable here.
  - Judge + grader are provider-agnostic: an OpenAI-compatible base_url + model +
    key (ensemble.http_call, keyed via functools.partial). Points at the local
    gateway, OpenAI, or Anthropic's OpenAI-compatible endpoint unchanged.

  RIGOR UPGRADE PATH (deliberately NOT built this chunk — demoable first):
    * PairwiseScorer: grader sees BOTH final answers and picks the better. It MUST
      blind them (strip model labels) and RANDOMIZE position (A/B order) per query,
      else position bias corrupts the result. Add it as a third Scorer impl.
    * Independent grader: point the grader at a DIFFERENT vendor than the frontier
      judge (e.g. GPT grades a Claude-vs-Gemma comparison) to kill self-bias.
    * N grader samples per item + variance/significance; expand the query set.
"""
from __future__ import annotations

import functools
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ensemble import EnsembleConfig, http_call, run_judge
from eval_queryset import QUERY_SET
import candidate_cache

QUERY_BY_ID = {q["id"]: q for q in QUERY_SET}
_HERE = os.path.dirname(os.path.abspath(__file__))
GEMMA_JUDGMENTS = os.path.join(_HERE, "eval_judgments_gemma.json")
FRONTIER_JUDGMENTS = os.path.join(_HERE, "eval_judgments_frontier.json")

_STOP = {"the", "a", "an", "is", "are", "to", "of", "and", "or", "in", "on", "for",
         "with", "it", "its", "as", "by", "be", "not", "no", "one", "so", "if",
         "you", "your", "that", "this", "at", "from", "how", "what", "use", "using"}


# --------------------------------------------------------------------------- #
# Scorers — pluggable. Each exposes .name and .score(query, answer) -> [0,1].
# --------------------------------------------------------------------------- #
def _terms(text: str) -> set[str]:
    toks = re.findall(r"[a-z0-9_]+", text.lower())
    return {t for t in toks if len(t) > 2 and t not in _STOP}


class LocalHeuristicScorer:
    """Fraction of the reference's key terms present in the answer. Deterministic,
    offline, zero deps — a CI/smoke backstop, NOT the thesis number: it is blind to
    correctness and synthesis and rewards keyword overlap. Use it to prove the
    pipeline runs; use ReferenceGrader for the real comparison."""

    name = "local_heuristic"

    def score(self, query: dict, answer: str | None) -> float:
        if not answer:
            return 0.0
        ref = _terms(query["reference"])
        if not ref:
            return 0.0
        return round(len(ref & _terms(answer)) / len(ref), 3)


class ReferenceGrader:
    """An LLM grades each answer 0-5 against the gold reference, normalized to
    [0,1]. Anchored to a fixed reference (not open A/B preference), so grader
    self-bias is limited. Provider-agnostic via a keyed OpenAI-compatible call."""

    name = "reference_grader"

    def __init__(self, base_url: str, model: str, api_key: str, timeout: float = 60.0,
                 call=http_call):
        self.base_url, self.model, self.timeout = base_url, model, timeout
        self._call = functools.partial(call, api_key=api_key)

    def score(self, query: dict, answer: str | None) -> float:
        if not answer:
            return 0.0
        msg = [{"role": "user", "content": (
            "You are grading one answer to a question against a reference answer. "
            "Score 0-5 how correct and complete it is (0=wrong/empty, 5=fully "
            "correct and complete). Judge substance, not style.\n\n"
            f"Question:\n{query['prompt']}\n\nReference answer:\n{query['reference']}"
            f"\n\nAnswer to grade:\n{answer}\n\n"
            'Respond ONLY with JSON: {"score": <0-5>, "reason": "<one sentence>"}')}]
        raw = self._call(self.base_url, self.model, msg, self.timeout,
                         response_format={"type": "json_object"})
        try:
            s = float(json.loads(raw).get("score"))
        except (ValueError, TypeError, AttributeError):
            return 0.0
        return round(max(0.0, min(5.0, s)) / 5.0, 3)


# --------------------------------------------------------------------------- #
# Judging over cached candidates (any judge config)
# --------------------------------------------------------------------------- #
def judge_over_cache(cache: dict[str, list[dict]], judge_cfg: EnsembleConfig,
                     call=http_call) -> dict[str, dict]:
    """Run one judge over every cached query's candidates. Returns
    {query_id: {answer, chosen, rationale, model}}. Skips ids not in QUERY_BY_ID."""
    out: dict[str, dict] = {}
    for qid, cands in cache.items():
        q = QUERY_BY_ID.get(qid)
        if not q:
            continue
        j = run_judge(q["prompt"], cands, judge_cfg, call)
        out[qid] = {k: j[k] for k in ("answer", "chosen", "rationale", "model")}
    return out


# --------------------------------------------------------------------------- #
# Scoring + report
# --------------------------------------------------------------------------- #
def evaluate(cache: dict, judgments: dict[str, dict[str, dict]], scorer,
             eps: float = 1e-6) -> dict:
    """judgments = {judge_name: {qid: judgment}}. Scores every judge's final answer
    per query, aggregates mean overall + per-category, and (for exactly 2 judges)
    a head-to-head win/tie count. Returns a JSON-able report."""
    judges = list(judgments)
    per_query, by_cat = [], {}
    for qid in cache:
        q = QUERY_BY_ID.get(qid)
        if not q:
            continue
        scores = {jn: scorer.score(q, judgments[jn].get(qid, {}).get("answer"))
                  for jn in judges}
        per_query.append({"id": qid, "category": q["category"], "scores": scores})
        cat = by_cat.setdefault(q["category"], {jn: [] for jn in judges})
        for jn in judges:
            cat[jn].append(scores[jn])

    mean = lambda xs: round(sum(xs) / len(xs), 3) if xs else 0.0
    aggregate = {jn: mean([pq["scores"][jn] for pq in per_query]) for jn in judges}
    by_category = {c: {jn: mean(v[jn]) for jn in judges} for c, v in by_cat.items()}

    report = {"scorer": scorer.name, "judges": judges, "n": len(per_query),
              "aggregate": aggregate, "by_category": by_category,
              "per_query": per_query}
    if len(judges) == 2:
        a, b = judges
        wins = {a: 0, b: 0, "tie": 0}
        for pq in per_query:
            da, db = pq["scores"][a], pq["scores"][b]
            wins[a if da - db > eps else b if db - da > eps else "tie"] += 1
        report["head_to_head"] = wins
    return report


def print_report(r: dict) -> None:
    print(f"\n=== JUDGE EVAL — scorer={r['scorer']}, n={r['n']} ===")
    print("aggregate mean score:")
    for jn, s in r["aggregate"].items():
        print(f"  {jn:20s} {s:.3f}")
    print("by category:")
    for c, d in r["by_category"].items():
        print(f"  {c:10s} " + "  ".join(f"{jn}={s:.3f}" for jn, s in d.items()))
    if "head_to_head" in r:
        print("head-to-head (per-query wins):", r["head_to_head"])


# --------------------------------------------------------------------------- #
# Offline self-check
# --------------------------------------------------------------------------- #
def demo() -> None:
    """No network: mock cache + two mock judges + both scorers, verify the
    pipeline shape, scoring bounds, and head-to-head tally."""
    qs = QUERY_SET[:4]
    cache = {q["id"]: [{"model": m, "content": f"cand {m}", "latency_s": 0.1,
                        "error": None} for m in ("coder", "reasoning", "general")]
             for q in qs}

    # Two judges: "strong" echoes the reference (high heuristic score); "weak"
    # returns a fixed off-topic string (low score). Proves the scorer discriminates.
    strong = {q["id"]: {"answer": q["reference"], "chosen": -1, "rationale": "",
                        "model": "gemma"} for q in qs}
    weak = {q["id"]: {"answer": "purple monkey dishwasher", "chosen": 0,
                      "rationale": "", "model": "frontier"} for q in qs}

    sc = LocalHeuristicScorer()
    for q in qs:
        assert sc.score(q, q["reference"]) > 0.5, "reference should self-score high"
        assert sc.score(q, "purple monkey dishwasher") < 0.2, "off-topic scores low"
        assert sc.score(q, None) == 0.0, "missing answer scores 0"

    rep = evaluate(cache, {"gemma": strong, "frontier": weak}, sc)
    assert rep["n"] == 4 and rep["judges"] == ["gemma", "frontier"]
    assert rep["aggregate"]["gemma"] > rep["aggregate"]["frontier"], "strong judge wins"
    assert rep["head_to_head"]["gemma"] == 4, "strong wins every query"
    assert set(rep["by_category"]) <= {"coder", "reasoning", "general"}
    assert all(0.0 <= s <= 1.0 for pq in rep["per_query"] for s in pq["scores"].values())

    # ReferenceGrader with a canned grader call (no network): returns a fixed JSON.
    def fake_grader(base_url, model, messages, timeout, response_format=None, api_key="none"):
        assert api_key == "testkey", "grader key threaded through"
        assert response_format == {"type": "json_object"}
        return json.dumps({"score": 4, "reason": "good"})
    rg = ReferenceGrader("http://frontier", "grader-x", "testkey", call=fake_grader)
    assert rg.score(qs[0], "anything") == round(4 / 5, 3), "grade normalized to [0,1]"
    assert rg.score(qs[0], None) == 0.0

    print("ok — judge_eval pipeline, both scorers, head-to-head verified offline")


def _frontier_from_env() -> tuple[str, str, str]:
    base = os.environ.get("JUDGE_URL", "https://api.openai.com")
    model = os.environ.get("JUDGE_MODEL", "gpt-5.2")
    key = os.environ.get("JUDGE_API_KEY") or os.environ.get("OPENAI_API_KEY") \
        or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        sys.exit("set JUDGE_API_KEY (or OPENAI_API_KEY / ANTHROPIC_API_KEY)")
    return base.rstrip("/"), model, key


def _load(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _save(obj: dict, path: str) -> None:
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()

    elif "--generate" in sys.argv:  # BOOT: candidates + in-fleet Gemma judge
        gw = os.environ.get("CONCLAVE_GW")
        if not gw:
            sys.exit("set CONCLAVE_GW=<ts-ip>:4000")
        gw = gw if gw.startswith("http") else f"http://{gw}"
        cfg = EnsembleConfig(gateway_url=gw, judge_model="general")  # Gemma
        cache = candidate_cache.populate(cfg)
        gemma = judge_over_cache(cache, cfg)
        _save(gemma, GEMMA_JUDGMENTS)
        print(f"cached {len(cache)} candidate sets + {len(gemma)} gemma judgments")

    elif "--frontier" in sys.argv:  # OFFLINE: frontier judge over cached candidates
        base, model, key = _frontier_from_env()
        cache = candidate_cache.load()
        if not cache:
            sys.exit("no candidate cache — run --generate on a boot first")
        cfg = EnsembleConfig(judge_url=base, judge_model=model)
        call = functools.partial(http_call, api_key=key)
        frontier = judge_over_cache(cache, cfg, call)
        _save(frontier, FRONTIER_JUDGMENTS)
        print(f"cached {len(frontier)} frontier judgments ({model})")

    elif "--score" in sys.argv:  # OFFLINE: score + compare
        cache = candidate_cache.load()
        judgments = {"gemma": _load(GEMMA_JUDGMENTS), "frontier": _load(FRONTIER_JUDGMENTS)}
        if "--heuristic" in sys.argv:
            scorer = LocalHeuristicScorer()
        else:
            base, model, key = _frontier_from_env()
            scorer = ReferenceGrader(base, model, key)
        print_report(evaluate(cache, judgments, scorer))

    else:
        sys.exit("usage: judge_eval.py [--demo | --generate | --frontier | --score [--heuristic]]")
