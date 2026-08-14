# Local coder — real-work failure log

**Purpose: turn "local is the daily driver, hosted is the escalation tier" from a hypothesis into an
operating rule with a trigger.** The 2026-07-17 pivot asserted that split on COST. There is still no
measured answer to *when to escalate*, because nothing has recorded where the local model actually
breaks on real tasks.

## Why a log and not a benchmark

T1–T3 (2026-07-28) established a capability FLOOR both the local 30B and the hosted 80B clear — and
therefore discriminates nothing above it. The obvious next move was "design a harder T4", but this
repo's own history says synthetic query sets saturate: the modern-fleet run was ceiling-limited at
25/30, and T1–T3 has now saturated too. A synthetic T4 would likely repeat that.

Real tasks do not saturate, cost nothing to obtain, and have a property no synthetic set has: **a
task that defeats the local model IS the harder benchmark**, already anchored to work that matters.

So: use the local coder on real work. When it fails, record it here. The failures are the escalation
policy, and they accumulate into the T4 that would otherwise have to be invented.

## The rule

Reach for the rented 80B when a REAL task defeats the local model — not on a hunch, and not on a
synthetic score. At that point the comparison is concrete and worth paying for.

## What to record

One entry per failure. Keep it short; an unwritten entry is worth nothing.

```
### YYYY-MM-DD — <one-line task>
- **Harness:** aider / Claude Code proxy / raw
- **Task shape:** e.g. multi-file refactor, single-function edit, read+summarise, test authoring
- **What it did:** the actual failure — wrong edit, confabulated completion, loop, gave up, too slow
- **Recoverable?** did a retry/reprompt fix it, or was it a hard stop
- **Escalated?** yes/no — and if yes, did the 80B (or frontier) actually do better
- **Class:** see below
```

## Failure classes seen so far (2026-07-28, from the T1–T3 work)

These came out of harness development rather than daily use, but they are real and they are the seed:

- **`confabulated-completion`** — claimed a multi-step task was done when it was not. Observed in the
  2026-07-17 CC run. This is the class that makes auto-accept unsafe.
- **`applied-but-wrong`** — edit lands and looks plausible, but breaks the file's own self-check.
  Observed run1 of T3: `AssertionError: dry-run generates nothing`. Only caught by RUNNING the result.
- **`context-driven-loop`** — asked for something not visible in context, the model guesses filenames,
  the harness auto-adds each, context inflates and the task runs to the wall-clock cap. Observed in
  `harness/results/t1t3-local30-t1loop/`. Partly a harness property, not purely the model.

Note what is NOT in this list: on the matched T1–T3 comparison the local model produced **zero** edit
rejects and passed every task. Its failures to date are about *judgement and self-report*, not about
mechanically driving a harness.

### 2026-07-28 — structured adversarial REVIEW is a hard escalation trigger

> ### 🔴 SUPERSEDED 2026-08-14 — read the entry below this one first.
> Two things were wrong with this entry. **(a)** It ran at `CONCLAVE_MAX_TOKENS=4096`; at the 16384
> a fair comparison needs, the same qwen build scores **0.127 (7/55, 1/8 criticals)**. **(b)** It
> was read as a fact about *the local tier* when it was a fact about *one model* — a same-footprint
> successor scores **0.309 with 5/8 criticals**. The `under-detection` class is real but it is
> **model-scoped, and it is largely closed by a model swap**, not by escalating to a bigger tier.

- **Task shape:** find planted defects in a PR diff and report them in a fixed schema
  (pr-arbiter's 20-PR corpus, 55 expected findings, their reviewer prompt verbatim).
- **What it did:** recall **0.073** (4/55), **0/8 criticals**, ~1 finding per PR where 3–5 exist.
  Measured by `orchestrator/s2_model_axis.py`; claude-sonnet on the identical task scores 0.509.
- **Class:** `under-detection` — a new class, and the sharpest capability gap measured so far.
  Note it is NOT confabulation: on all 3 negative-control PRs it correctly reported zero findings,
  and its output parsed cleanly every time. It misses issues; it does not invent them.
- **Recoverable?** Not by retry — the volume gap is consistent across all 17 non-control PRs.
- **Escalated?** n/a (this was a measurement, not a work task).
- **Why this one matters:** T1–T3 showed the local model matching the hosted 80B on *edit-and-apply*
  tasks. This shows an ~7× gap on *find-the-defect* tasks. **Task shape, not model tier, is what
  should drive escalation** — and review is the shape that breaks it.

### 2026-08-14 — the review gap is ~1.65×, not ~7×, and a model swap closes most of it

Re-ran the identical probe (same corpus, same verbatim prompt, same scorer, same matcher) on a
successor that did not exist when the entry above was written, plus a matched-budget control on the
original model. All three arms at `CONCLAVE_MAX_TOKENS=16384`. $0, ~40 min of laptop time.

| reviewer | recall | matched/55 | criticals | FP |
|---|---|---|---|---|
| claude-sonnet (reference) | 0.509 | 28 | 6/8 | 30 |
| **`muse-glimmer:30b`** | **0.309** | 17 | **5/8** | 15 |
| `qwen3-coder:30b` (control, matched budget) | 0.127 | 7 | 1/8 | 13 |
| `qwen3-coder:30b` @ 4096 (the retracted figure) | 0.073 | 4 | 0/8 | 16 |

- **What changed the number most: the model.** 0.127 → 0.309 at identical budget, and criticals
  1/8 → 5/8. Ten findings and four criticals apart is outside the 1–4 draw spread arbiter measured,
  so this is not sampling noise.
- **What changed it partly: the token budget.** Muse Glimmer is a *thinking* model — reasoning and
  content share the completion budget, so 4096 measures truncation, not capability. Raising it for
  the new arm forced re-running the old arm to match. The 4 → 7 shift on qwen *is* inside the draw
  spread, so **do not claim the budget caused it**; claim only that 0.073 is not the matched-budget
  figure.
- **Class:** `under-detection` **downgraded from "hard escalation trigger" to "model-dependent
  deficit"**. It still under-detects relative to claude, but it now catches most criticals, and its
  false-positive rate is *better* than claude's (15 vs 30).
- **Escalated?** n/a — a measurement. But the operating rule it feeds changes: review is no longer
  a shape that *breaks* the local tier, it is one where the local tier runs at ~2/3 recall for free.

**The methodological lesson, which is the durable part.** The original entry survived four months
and propagated into two sibling repos as "the local tier cannot review." It was a single draw, on
one model, at a budget nobody had matched. The instrument was always able to falsify it for $0 —
nothing re-ran it because the number was *convenient*: it cleanly justified escalation policy and
it flattered a guard. **A number that settles an argument is the one to re-run first.**

## Anti-over-claiming

- n is small and grows only as fast as real work happens. This is observational, not a controlled
  experiment — confounds everywhere, and it cannot produce a quality *ranking*.
- A failure here is evidence about a TASK SHAPE, not a verdict on the model.
- Do not aggregate these into a "local vs hosted" score. That is exactly the number T1–T3 showed this
  project cannot currently measure, and inventing one from anecdotes would be worse than having none.
