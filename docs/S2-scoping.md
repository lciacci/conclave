# S2 (union-recall divergence variant) — scoping memo

> **⚠️ 2026-07-28:** pr-arbiter is CLOSED as a research project; `arbiter` is a real TOOL and no
> longer a gate. This memo's framing of S2 as feeding *"Tessera's is-review-fan-out-worth-it gate"*
> is therefore stale. The MEASUREMENT below stands and its result is unaffected — only its STATUS
> changes, from gate input to design input for the tool.
>
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

**Next real unblock** was a cross-repo coordination session (thicken n / settle D1). **It happened
2026-08-07** — see `docs/HANDOFF.md` top block. The PARK still stands and D1 is still open; what
changed is that the port is no longer gating anything, because `arbiter` is a shipping tool and is
not gated. The one open measurable is the **peer-strength** arm of the addendum below.

## Addendum (2026-07-20) — a MODEL axis, if S2 is ever built

pr-arbiter predates the idea of different MODELS per adversarial role (owner, 2026-07-20). If S2 is built,
it extends naturally to measure that axis: **union-recall(role only) vs union-recall(role × model) vs
best-single**, on the same synthetic corpus, $0 (local-qwen generates the extra model's passes). This is
the one variant that could add NEW evidence rather than porting pr-arbiter's existing role-only number —
so if S2 is built at all, build it with MODEL as a variable, not just ROLE.

Caveats carried from HANDOFF (2026-07-20 top block): (1) it challenges anti-conflation **guard (b)** ("ROLE
not MODEL") — but the guard's evidence is the SELECT-BEST null, not union-recall, so the question is genuinely
open. (2) The skeptic's null: role-diversity may already capture the decorrelation; models converge — marginal
gain over role-alone could be ~0. (3) Rewriting guard (b) is an ADR-level, cross-repo decision (co-owned +
Tessera-hosted) — this memo records the axis, it does not resolve the guard. (4) Excitement-bias: this is the
router thesis reborn; gate on the instrument, not the instinct.

## Result (2026-07-28) — the MODEL axis was measured. It added NOTHING.

The addendum's variant was built (`orchestrator/s2_model_axis.py`) — NOT the parked port. Arms matched
at TWO passes each, so this cannot be the candidate-count confound that retracted Self-MoA's +0.0977:

| arm | recall | matched/55 | crit | FP |
|---|---|---|---|---|
| best single — claude reviewer | 0.509 | 28 | 6/8 | 30 |
| **ROLE**-diverse union: claude-reviewer ∪ claude-arbiter | **0.618** | 34 | 7/8 | 35 |
| **MODEL**-diverse union: claude-reviewer ∪ qwen-reviewer | **0.509** | 28 | 6/8 | **50** |
| qwen reviewer alone | 0.073 | 4 | 0/8 | 16 |

**MODEL diversity: +0.000 recall, +20 false positives. Zero decorrelated catches** — every true
finding qwen produced was already in claude's set. ROLE diversity on the same corpus, same scorer,
same pass count: +0.109 and the 8th critical.

Scorer validation: this reproduces pr-arbiter's own committed numbers to 4 dp (0.5091 / 0.6182) using
their `_approximate_match` and their `expected_findings`, so it is their definition of a true finding,
not a new one.

**BOUND — do not over-read this.** It tests *adding a much WEAKER model* (0.073 vs 0.509, ~7×), not
model diversity among peers. A weak second model cannot add union-recall because its findings are a
subset. So this is **directionally supportive of anti-conflation guard (b) but does NOT settle it.**
The open question is unchanged in shape and now sharper: *does a second PEER-STRENGTH model
decorrelate?* That needs a second frontier model and therefore money — it is not $0 like this was.

Guard (b) is NOT rewritten here (boundary 4). No threshold is emitted (boundary 1). This records a
measurement for whoever writes the ADR.
