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

## Anti-over-claiming

- n is small and grows only as fast as real work happens. This is observational, not a controlled
  experiment — confounds everywhere, and it cannot produce a quality *ranking*.
- A failure here is evidence about a TASK SHAPE, not a verdict on the model.
- Do not aggregate these into a "local vs hosted" score. That is exactly the number T1–T3 showed this
  project cannot currently measure, and inventing one from anecdotes would be worse than having none.
