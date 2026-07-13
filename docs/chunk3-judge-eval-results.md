# Chunk 3 — judge eval results (v3 thesis)

**Question:** does the in-fleet **Gemma-9B** judge hold up against a **frontier**
judge (Claude Sonnet 5) at selecting/synthesizing across the three specialists'
answers?

> **Status: superseded by the rigor pass (2026-07-12).** The original n=18 result is
> kept below for the record, but its headline was **too generous** and one of its
> three caveats turned out to be **false**. Read the rigor pass first.

---

# Rigor pass (2026-07-12) — the defensible number

**Setup.** Query set expanded **18 → 36** (12/12/12). The 18 new queries are
deliberately **trap questions** — ones with a fluent wrong answer a weak judge reaches
for (Monty Hall, the 40-vs-45 average-speed trap, bare `except`, the pigeonhole socks).
One boot generated candidates for the new queries only; the original 18's judgments were
**carried over untouched**, so n=36 is a strict superset of the published n=18, not a
re-run of it. Grading: **3 samples per answer** with variance. Frozen in
`orchestrator/eval_fixtures/`; re-score for **$0** with `judge_eval.py --score` (the
grader memo is committed, so no API calls).

> ## ⚠️ READ THIS BEFORE QUOTING ANY NUMBER BELOW
>
> A later adversarial review (2026-07-12, three independent reviewers) found that **this
> eval does not measure what its headline says it measures.** The number reproduces; the
> *interpretation* was wrong in three ways. Corrections are inline below and summarized
> in "What the evidence actually supports". The short version:
>
> 1. **The two judges are not doing the same task.** The frontier judge sets
>    `chosen == -1` on **34 of 36** queries — it *ignores the candidates and writes its
>    own answer*. Its 1.000 is substantially "Claude Sonnet 5 answers an easy question
>    that has a gold reference", **not** judging skill. **Gemma therefore cannot win**;
>    "0 wins" is arithmetic, not a finding.
> 2. **The error bar was the wrong statistic**, overstating precision ~4×.
> 3. **The "2.6× discrimination" headline is not statistically significant** (p = 0.146),
>    and the trap questions did not function as traps.

## Result — n=36, reference-graded (grader: Claude Sonnet 5)

| | Gemma judge | frontier judge |
|---|---|---|
| **reference-grade (0–1)** | **0.883** | **1.000** |
| — coder | 0.789 ± 0.090 | 1.000 |
| — reasoning | 0.911 ± 0.051 | 1.000 |
| — general | 0.950 ± 0.026 | 1.000 |
| head-to-head | 0 win / 25 tie / 11 loss — **but see the ceiling caveat** | |

**The error bar that bounds the claim** (paired over the 36 items, which is the sample
that matters — the queries are a sample from a population of possible queries):

| | |
|---|---|
| paired gap (frontier − gemma) | **0.117** |
| SEM | **0.0366** |
| 95% CI on the gap | **[0.045, 0.188]** |
| t (35 df), p | 3.19, **p ≈ 0.003** |

**The gap is real — but it is 3.2× its error bar, not 12×, and the CI admits a gap as
small as 0.045.** An earlier version of this doc quoted `± 0.010` as the resolution floor.
That is the *grader replication noise* (how much one grader wobbles when asked twice —
and 33 of 36 items have stdev exactly 0.000), which says nothing about sampling error
across queries. The real resolution floor is ≈0.07 (2 SEM), ~7× larger. `judge_eval.py`
now prints the paired SEM alongside, precisely so this is not misread again.

## What the rigor pass changed — and where it over-claimed

**1. THE DEEPEST FLAW: the two judges are not performing the same task.** This was missed
by the rigor pass and found by a later adversarial review. It undercuts the whole
comparison:

| | frontier (sonnet-5) | gemma |
|---|---|---|
| `chosen == -1` — synthesized, **ignored the candidates** | **34/36** | 2/36 |
| mean answer length | 995 chars | 491 chars |

The frontier judge **writes its own fresh answer on 34 of 36 items.** So its 1.000
substantially measures *"Claude Sonnet 5 answers an easy question that has a gold
reference"* — not judging skill. Its perfect score is over-determined, and **this, not the
0–5 scale, is the real mechanism behind the ceiling.**

The consequence is severe: with the frontier pinned at the metric maximum on all 36 items,
the head-to-head collapses to *"count of items where Gemma < 1.0."* **Gemma cannot win.**
Reporting "0 wins / 25 ties / 11 losses" as a two-sided comparison is misleading — it is a
one-sided count.

**The single highest-leverage fix is not pairwise.** `EnsembleConfig.mode` already supports
`"select"`. Running the eval in *select* mode — or grading `chosen` against a per-query
best-candidate label — would force both judges to actually **judge**, removing the ceiling
**without needing any third-party grader key at all**. That reframes the open work below.

**2. "The query set was too easy" — DIRECTIONALLY RIGHT, but the headline number is NOT
SIGNIFICANT.**

| block | Gemma mean | Gemma loses |
|---|---|---|
| original 18 | 0.915 | 3/18 (17%) |
| new 18 (traps) | 0.852 | 8/18 (44%) |

An earlier version of this doc called this "**2.6× better discrimination**" and "the single
most important finding". **It does not survive a significance test:** Fisher exact on 3/18
vs 8/18 gives **p = 0.146**; Welch on the block means gives **p ≈ 0.40**. The "2.6×" is
8/3 on raw counts, with a risk-ratio CI spanning roughly [0.8, 9] — entirely
noise-compatible at n=18 vs n=18. It is also confounded: the blocks differ in question
*form* (the new coder items are "why?" explanation questions).

**And the traps are not traps.** All three specialists answered *every* trap question
correctly (checked: avg-speed, Monty Hall, lily-pad, work-rate, clock-angle, socks,
bat-ball). No fluent wrong answer was ever on the table for a judge to fall for, so the
queryset's stated premise was never exercised. What the trap block actually measured was
whether the judge's *write-up* was complete enough to earn 5/5 against a more explanatory
reference — e.g. Gemma's Monty Hall answer, *"Yes, you should switch."*, is **correct** and
scored 2/5 for terseness.

**3. "The grader self-bias caveat was FALSE" — OVER-CLAIMED. Correct wording: *no
self-bias was detected, by an instrument that cannot detect it where it would show.***

- *Gemma side:* sound in kind. Gemma sits well below the ceiling, so a grader had room to
  move it and didn't (Gemini 0.860 vs Anthropic 0.847, +0.013). Real, if weak (n=10),
  evidence that reference grading is robust across vendors.
- *Frontier side (+0.000):* **not a refutation.** The frontier is pinned at the top of the
  scale for *both* graders. **You cannot measure upward inflation on a variable that
  cannot go up.** A self-biased Sonnet and a lenient Gemini produce the identical
  observation; the test does not discriminate the hypotheses. This is in direct tension
  with the ceiling finding — both can be literally true, but "the caveat was FALSE" is an
  inference the ceiling *forbids*.
- **The n=10 Gemini grades are not committed.** All 216 entries in the grade cache key to
  `api.anthropic.com`. The claim that overturned a published caveat has **no committed
  artifact and cannot be replayed.** Treat it as an unreplicated note, not a result.

**4. Data hygiene, in the published run.** `gen-apology-email`'s Gemma answer is a JSON
*object*, not a string; it was `str()`-formatted into the grader prompt as a **Python dict
repr** and graded 0.8. `reason-socks-pigeonhole`'s Gemma answer contains a literal
`<h1>3</h1>`. Fixed at the boundary in code (`_as_text`), but the **published 0.883 includes
that dict-repr grade** — it is one item of 36 and does not move the conclusion, but it is a
blemish on the frozen artifact and is recorded here rather than quietly re-graded.

**5. "Concentrated in code (0.789)" — suggestive, not established.** Per-category SEMs at
n=12: coder 0.789 ± 0.090, reasoning 0.911 ± 0.051, general 0.950 ± 0.026. Coder's 95% CI
is roughly [0.61, 0.97] and **overlaps reasoning's.**

**6. "Losing 11 of 36" reads as 11 judging failures. The data support about 4.**
2 genuine correctness failures (`coder-bigo-nested` — "O(n) … due to the hash table", flatly
wrong; `coder-git-undo-commit` — a recipe that leaves the commit on the wrong branch),
2 genuine incompleteness on "why?" questions, and **7 are 4/5-vs-5/5 nits on answers that
are substantively correct.** The grader rubric asks for "correct **and complete**" against
references containing full derivations, while the judge prompt asks only for "the single
best answer" — a rubric mismatch that penalizes terseness, not error.

## Bias control — the design, and why it is not the usual one

The obvious fix for grader bias is "use a neutral third vendor". We could not: the only
available key was **Gemini**, and **Gemma is a Google model** — so a Gemini grader shares
a house with *the local judge*, biasing toward our own thesis, which is the direction a
skeptic attacks first. (A naive `grader_host != judge_host` check waves this through;
grader bias is a **vendor** property, not a hostname one. `_grader_bias()` encodes this.)

So `--bracket` runs **both biased graders as bounds** instead of pretending to neutrality:

- **Anthropic grades** → inflates the frontier judge → Gemma's score is a **lower bound**.
- **Gemini grades** → inflates Gemma (same house) → Gemma's score is an **upper bound**.

A conclusion that survives *both* ends is robust to grader bias in either direction — a
stronger claim than any single grader supports. The n=36 number above is the **lower
bound** (Anthropic-graded), i.e. the conservative, hardest-on-Gemma reading. The Gemini
upper-bound arm is quota-blocked; the n=10 subsample says the two bounds nearly coincide.

## Open — blocked on grader quota

`PairwiseScorer` (blinded, position-debiased) is **built, tested, and unrun**. The Gemini
free tier is ~**20 requests/day/model**; the pairwise arm needs 72+ calls. This is a daily
quota, not a rate limit — **no pacing fixes it**. Unblock with either:

- **billing on the Gemini key** (cents at flash pricing), or
- **an OpenAI key** — a genuinely third house, neutral to *both* Anthropic and Google,
  which removes the need for `--bracket` entirely.

Escalation: `esc-20260713-025337`.

## Harness notes (what the rigor pass had to fix to get here)

Three **pre-existing bugs in committed code**, all found by trying to run it rigorously:

1. **`claude-sonnet-5` now rejects `temperature` outright** ("deprecated for this model").
   `frontier_call` hardcoded `temperature=0`, so **the `--frontier` phase was already
   broken** — the frozen Chunk 3 run predates the deprecation. Now sends it and retries
   without on rejection.
2. **No retry anywhere.** A bracket run is 200–400 sequential calls against rate-limited
   keys; runs died at 4:41 and 5:26 to a single transient 503/429 and lost everything.
   Now backs off honoring `Retry-After`, and **paces under RPM caps** (`_Throttle`) —
   bursting into a limit and backing off is the wrong shape for a per-minute cap.
3. **`judge_over_cache` re-judged everything.** Growing the query set would have silently
   **rewritten the frozen 18's judgments**, breaking comparability with the published
   result and invalidating every cached grade. Now incremental; `refresh=True` to override.

Plus **`GradeCache`** — a disk memo of grader verdicts, the same "iterate with the
expensive thing switched off" trick `candidate_cache` applies to the GPU. It is what makes
a rate-limited 432-call eval finish at all (runs resume instead of restarting) and what
makes re-scoring cost **zero** API calls.

### Code review (PR #9) — six more, all "silent wrong number" bugs

Review found six bugs whose failure mode is a *plausible but wrong published number*
rather than a crash. **None affected the number above** (the fixtures are 36/36 for both
judges with one judge model throughout), but each was live on the documented next steps:

1. **A failed judge was cached as a judgment.** `run_judge` degrades to a raw specialist
   answer on any exception — right for serving (never sink the ensemble), catastrophic
   for an eval. A stale key 401s, every "frontier judge" row becomes *the coder's raw
   answer*, and we grade and publish it as the judge's output — and the incremental
   carry-over then never retries it. Failed rows are no longer cached.
2. **A query missing from one judge's file scored 0.0 instead of being skipped.** Directly
   on the growth path: expand the query set, `--generate`, then score without re-running
   `--frontier` → the frontier takes a 0.0 on every new query and Gemma "wins" a
   comparison it never had. `evaluate()` now scores only queries **every** judge answered,
   warns, and records `skipped_unjudged` in the report.
3. **Swapping `JUDGE_MODEL` silently mixed models in one judgments file** (prior rows
   carried over regardless of model; report labelled with the new model only). Mismatched
   priors are now dropped and re-judged.
4. **The vendor guard was exact-host**, so Gemini via **Vertex** (`*.googleapis.com`) or a
   reseller read as "independent" of Gemma — the same hole the vendor table exists to
   close, one layer down. Now suffix-matched, and an unestablishable host returns
   **`UNVERIFIED`**: *unknown is not unbiased*, and `--pairwise` refuses it.
5. **The grade-cache key omitted `base_url` and the reference text** — `--bracket` shares
   one cache across two graders (same model string on two vendors collided), and editing a
   gold reference while keeping its id silently reused every stale grade.
6. `RuntimeError("unreachable")` was reachable (a `temperature` rejection on the final
   retry attempt fell out of the loop).

Fixing (5) invalidated the committed grade cache. Re-grading would have re-queried a
nondeterministic grader and could have **silently moved a published number**, so the 216
grades were **migrated** to the new keys with identical values instead. Verified: `--score`
replays the fixtures with an *invalid* API key and reproduces 0.883 ± 0.010, 25/36 tied,
0 live calls. Regression tests cover all six.

---

# Original result (2026-07-11, n=18) — superseded, kept for the record

**Setup.** 18 labeled queries (6/6/6), each with a gold reference. One boot fanned every
query to the fleet and ran the in-fleet Gemma judge. Offline: the Sonnet frontier judge
ran over the same cached candidates, then a reference-anchored Sonnet grader scored both.

| scorer | Gemma judge | frontier judge |
|---|---|---|
| reference-grade (0–1) | 0.89–0.91 | 1.00 |
| head-to-head | 0 win / 15 tie / 3 loss | |
| local heuristic (keyword) | 0.37 | 0.69 |

**Claimed finding: "the small self-hosted judge holds up."** Read the rigor pass — the
tie rate was inflated by easy questions, and it drops from 83% to 56% once the query set
discriminates.

The **local heuristic disagrees loudly** (frontier 0.69 vs Gemma 0.37) — but it scores
keyword overlap with the reference, not correctness, and the frontier judge simply
phrases more like the reference. That gap is a caution about the heuristic, not evidence
about judge quality; it remains a CI/smoke backstop only.

**Its three caveats, adjudicated by the rigor pass:**

1. ~~Grader self-bias inflates the frontier, so the true gap is smaller.~~ **FALSE** — an
   out-of-house grader reproduces the frontier's 1.000 exactly (+0.000). See above.
2. ~~Single grader sample (0.911 → 0.889 across runs).~~ **FIXED** — 3 samples, ±0.010.
3. ~~n=18, reference-anchored not pairwise.~~ **HALF-FIXED** — n=36 now, and the harder
   queries changed the conclusion. Pairwise remains blocked on grader quota.

## Standing conclusion — what the evidence actually supports

> Over 36 queries, a Sonnet grader anchored to gold references scores Gemma's judged
> answers **0.883**; the frontier judge's answers score **1.000 on every item** — but it
> reaches that by **writing its own answer rather than judging** (`chosen == -1` on 34/36),
> so the metric is **saturated and cannot rank the two judges**. The gap of **0.117 (95% CI
> [0.045, 0.188], p ≈ 0.003)** is real *under this rubric*, but it mixes ~4 genuine judging
> failures with ~7 completeness deductions on substantively correct answers. The trap block
> did not function as a trap (every specialist answered every trap correctly), and its
> apparent extra discrimination (p = 0.146) is not distinguishable from noise. Self-bias
> was **not detected — by an instrument that cannot detect it at the ceiling** — and the
> out-of-house grades supporting that claim are not committed.

**So: is the self-hosted judge good enough?** *This eval cannot answer that*, and neither
the original "the small judge holds up" nor the rigor pass's "a real 0.12 gap" should be
quoted as if it did. What can be said honestly: Gemma produces materially shorter answers
that a completeness-weighted rubric marks down, it has **two demonstrable correctness
failures on code** out of 36, and it never beats a frontier judge that isn't actually
judging. Frontier remains the **baseline to beat**, never a runtime dependency.

## The fix, and it is not the one we thought

The open work was "run pairwise, blocked on a grader key". That is **no longer the highest
-leverage step.** The ceiling is caused by the frontier judge *answering instead of
judging*, and the cure is already in the codebase:

- **Run the eval in `mode="select"`** (`EnsembleConfig.mode` supports it today), or grade
  `chosen` against a per-query best-candidate label. Either forces both judges to actually
  **judge**, which removes the ceiling and makes "who is the better judge" answerable —
  **with no third-party grader key and no GPU boot.**
- Pairwise (blinded, both-orders) is still built, tested, and worth running afterwards — it
  is the more sensitive instrument once both sides are doing the same task. It remains
  blocked on a neutral grader key (see below).

Doing pairwise *first*, on a task where one judge doesn't judge, would produce a
more precise measurement of the wrong thing.
