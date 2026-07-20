# S2 (union-recall divergence variant) — scoping memo

> **Status:** scoping only, no build. Produced 2026-07-20 from a read of the canonical contract
> (`../tessera/docs/contracts/three-project-cohesion.md`) + pr-arbiter's code/eval + Tessera's gate
> framing. Purpose: so S2 is never built blind, and so the "labels are the blocker" misread doesn't
> recur. **Recommendation: PARK the build.** See the bottom.

## What S2 is (contract seam)

A **scoring variant** of `orchestrator/divergence.py` whose oracle is the **union of true findings**
(bug-recall + false-positive-rate vs a labeled defect set), NOT best-single-*answer*. Same instrument
shape as the model-fleet divergence; different scoring function. It feeds Tessera's *"is review-fan-out
worth it?"* gate. Conclave owns the instrument shape; the **"true finding" scoring function is co-owned
with pr-arbiter** (it defines a finding).

## Not blind — the four things now pinned

- **Objective (pr-arbiter):** reviewer → independent arbiter → mutual KEEP/DROP triage. **One model,
  role-differentiated prompts** (all `claude-sonnet-4-6`) — ROLE diversity, not MODEL. Union via
  `merge_findings()` (dedupe file+category+line-midpoint ±3, keep higher severity).
- **Metric:** recall over the merged/union finding set vs `corpus/pr_XXX/rubric.json`
  `expected_findings`; a "catch" = same file + category + midpoint within ±3 (`eval/harness.py`
  `_approximate_match`). Also precision, **critical-recall (unweighted)**, negative-control FP rate.
- **Data that ALREADY EXISTS:** 20 planted-bug PRs, 55 expected findings (8 crit / 11 high / 18 med /
  18 low); committed `results/iter{1,2,3}_*.json` **already contain role-diverse passes from one model**
  (reviewer / arbiter / two triage voices).
- **Shape:** agent input `{pr_id, before, after, diff, lang}`; rubric finding
  `{id, category, severity, file, line_range:[a,b], description}`; result per-PR
  `{reviewer_findings[], final_findings[], score}` (triage runs also store `merged_findings`,
  `reviewer_votes`, `arbiter_votes`).

## The correction (why "labels are the blocker" was only half true)

There are **two** corpora, easy to conflate:
1. **Phase-1 synthetic** — *exists*, labeled, with a *working recall harness*. Source of "7/8 vs 6/8
   criticals." A first union-recall measurement is buildable on this **today, $0, no annotation pilot**
   — caveat: ground truth is synthetic planted bugs, not real review comments.
2. **Phase-3 real-PR / maintainer-comment** — does *not* exist; needs the 8–15h senior-annotator pilot;
   the formal F1-vs-union metric is design-only (`../pr-arbiter/docs/PHASE_3_DESIGN.md`). *This* is the
   labeled corpus the earlier "blocker" claim was really about.

## The catch that decides value

**pr-arbiter's `harness.py` already computes union-recall vs best-single on the labeled set.** "7/8 union
vs 6/8 single" IS a headroom-shaped result. So conclave's S2 is largely a **PORT of pr-arbiter's existing
recall scorer into the `divergence.py` frame** — it buys *one instrument across model-fleet and review
headroom* + comparability, **NOT new evidence.** Observatory line: *"the FRAME helps, the current METRIC
pollutes"* — adopt pr-arbiter's finding-scoring into the frame; do NOT port select-best.

## The real weakness is n, not code

The headroom evidence already exists and is **thin** (anti-conflation guard d): **+1 critical, 1 seed, 20
synthetic PRs.** The load-bearing lever for the ADR is **thickening n** (multi-seed the existing corpus —
cheap, and local-qwen-generatable now) and eventually the real corpus (blocked, expensive) — NOT the port.

## Hard boundaries (from the contract — do not cross)

1. **Emit the MEASUREMENT, not a go/no-go threshold.** A pass/fail cutoff is routing policy (guard c),
   Tessera's lane. No threshold exists by design; it's D2's ADR job.
2. **Don't define "true finding" solo.** Reuse pr-arbiter's `expected_findings` + `_approximate_match`
   — using theirs IS the co-ownership-safe path (a lane change needs their sign-off).
3. **Don't reproduce select-best** (guard a). Swap the oracle to union-of-true-findings, or you rebuild
   the polluting metric.

## Recommendation — PARK

Don't build S2 now. Three reasons, each sufficient:
1. The number it produces **already exists** (thin); the port adds comparability, not evidence.
2. The real lever (more seeds) is **pr-arbiter's harness/lane** — a cross-repo coordination move, not a
   conclave solo build.
3. Everything downstream (D1 interop shape, pr-arbiter Phase 3, a standing fleet) is **ADR-gated/deferred**
   — conclave's piece would ship and sit unused.

**Only flip:** if a **demo** is wanted, the unified "one divergence tool across model + review headroom"
is genuinely demoable — build the port for the story, not the evidence.

**Next real unblock** is a cross-repo coordination session (thicken n / settle D1), owner-driven — not a
conclave task. This memo is the pre-build artifact for whenever that happens.
