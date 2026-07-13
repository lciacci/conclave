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

## Result — n=36, reference-graded (grader: Claude Sonnet 5)

| | Gemma judge | frontier judge |
|---|---|---|
| **reference-grade (0–1)** | **0.883 ± 0.010** | **1.000 ± 0.000** |
| — coder | 0.789 | 1.000 |
| — reasoning | 0.911 | 1.000 |
| — general | 0.950 | 1.000 |
| head-to-head | **0 win / 25 tie / 11 loss** | |

`±` is the mean per-item grader stdev over 3 samples. **A gap smaller than ±0.010 is
not resolved by this eval.** The 0.117 gap is ~12× that, so it is real signal, not noise.

## What the rigor pass actually changed

**1. The query set was too easy, and that inflated the original result.** Splitting
n=36 by block:

| block | Gemma mean | Gemma loses |
|---|---|---|
| original 18 | 0.915 | 3/18 (17%) |
| new 18 (traps) | 0.852 | **8/18 (44%)** |

The trap block discriminates **2.6× better**. The Chunk 3 headline — "ties the frontier
on 15 of 18" — was substantially a statement about the *questions*, not the judge: a
query both judges answer correctly cannot tell them apart. Under questions that actually
separate them, Gemma's deficit widens and its tie rate collapses. **This is the single
most important finding of the rigor pass**, and it cuts against the thesis.

**2. The "grader self-bias" caveat was FALSE.** Chunk 3 assumed Sonnet's perfect 1.000
for its own judge was self-flattery, and that the true gap was therefore *smaller* than
shown. It is not. An **out-of-house grader (Gemini, no stake in Claude)** scores the
frontier judge **1.000 as well** — self-bias inflation **+0.000** (n=10 subsample). It
barely flatters Gemma either (0.860 vs Anthropic's 0.847, +0.013). Two graders from
different houses agree closely, which is a **validity signal for reference-anchored
grading**: the instrument is robust across vendors, and Gemma's deficit is real, not a
grading artifact. The gap is not smaller than shown. If anything it was understated,
because the questions were easy.

**3. The real instrument flaw is a CEILING EFFECT, not bias.** The frontier judge
saturates at 1.000 **for both graders**, on every one of 36 queries, with zero variance.
Reference-anchored 0–5 grading cannot resolve the top of its own scale — it can show
*that* Gemma trails but not *how far*, and it can never show the frontier improving.
**Pairwise (blinded, both-orders) is the instrument that fixes this.** It is built and
tested, and it is blocked only on grader quota (below).

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

## Standing conclusion

Gemma trails the frontier judge by **~0.12 absolute, losing 11 of 36 and winning none** —
a real gap, concentrated in **code** (0.789). That is a **weaker** result than Chunk 3
claimed. Whether it is *good enough* is a product call, not a measurement: a self-hosted
judge that ties a frontier judge on 25 of 36 queries at zero marginal cost may still be
the right default, and frontier remains the **baseline to beat**, never a runtime
dependency. But "the small judge holds up" should not be stated without the coder number
and the ceiling caveat next to it.
