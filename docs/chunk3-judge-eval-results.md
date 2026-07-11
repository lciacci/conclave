# Chunk 3 — judge eval results (v3 thesis)

**Question:** does the in-fleet **Gemma-9B** judge hold up against a **frontier**
judge (Claude Sonnet 5) at selecting/synthesizing across the three specialists'
answers?

**Setup (2026-07-11).** 18 labeled queries (6 coder / 6 reasoning / 6 general),
each with a gold reference. One boot fanned every query to the fleet and ran the
in-fleet Gemma judge (candidates + judgments cached in `orchestrator/eval_fixtures/`).
Offline afterward: the Sonnet frontier judge ran over the same cached candidates,
then a **reference-anchored grader** (Sonnet, scores each final answer 0–5 vs the
reference) scored both. Data + report frozen in `orchestrator/eval_fixtures/`;
reproduce with `judge_eval.py --score` against those files.

## Result

| scorer | Gemma judge | frontier judge |
|---|---|---|
| **reference-grade (0–1)** | **0.89–0.91** | 1.00 |
| — coder | 0.77 | 1.00 |
| — reasoning | 0.97 | 1.00 |
| — general | 1.00 | 1.00 |
| head-to-head (per query) | **0 win / 15 tie / 3 loss** | |
| local heuristic (keyword) | 0.37 | 0.69 |

**Finding: the small self-hosted judge holds up.** Gemma scores ~0.9 absolute and
**ties the frontier on 15 of 18 queries**, matching it on general and reasoning and
trailing only on **code selection/synthesis** (0.77) — where correctness is subtle
and a 9B judge is likeliest to misjudge. A frontier judge is not a large win here.

The **local heuristic disagrees loudly** (frontier 0.69 vs Gemma 0.37) — but it
scores keyword overlap with the reference, not correctness, and the frontier judge
simply phrases more like the reference. That gap is a caution about the heuristic,
not evidence about judge quality; the heuristic is a CI/smoke backstop only.

## Caveats (why this is demoable, not yet rigorous)

1. **Grader self-bias.** The grader (Sonnet) grades the frontier judge's (Sonnet)
   own output → a perfect, non-discriminating 1.000. The true Gemma–frontier gap is
   therefore likely **smaller** than the ~0.1 shown; Gemma is penalized relative to a
   judge the grader never marks down. **Fix:** a cross-vendor independent grader
   (e.g. Gemini or GPT grading a Claude-vs-Gemma comparison).
2. **Single grader sample.** Re-running moved Gemma 0.911 → 0.889 — grader
   non-determinism. Rigor needs N samples + variance/significance.
3. **n = 18, reference-anchored (not pairwise).** Small set; absolute 0–5 grading is
   less sensitive than blinded head-to-head. Rigor path: a `PairwiseScorer` (blinded,
   position-randomized), more queries.

The upgrade path is a config/scorer swap, not a rewrite — see `judge_eval.py`'s
header. Frontier is the **baseline to beat**, never a default in the serving path;
this result is the evidence that motivates keeping the judge self-hosted.
