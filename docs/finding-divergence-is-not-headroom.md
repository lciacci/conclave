# Disagreement is cheap. Complementarity is rare.

### What I learned trying to build a judge for a multi-model ensemble — and why I didn't build it

---

## TL;DR

I built a three-model ensemble to test a pattern I find genuinely interesting: **fan a query
out to several specialists in parallel, then have a "judge" model select or synthesize the
best answer.** Meta-reasoners over specialized outputs.

Before building the judge, I stopped to ask a question nobody in the pattern's literature
seems to ask first:

> **Does this fleet give a judge anything to arbitrate?**

It doesn't. And the way it doesn't is the interesting part.

- The three models **disagree on 80% of queries.** They are not clones.
- But **when they disagree, the same model is almost always the one that's right.**
- So a **perfect** judge — an oracle that always picks the best of the three — beats *just
  always calling that one model* by **+0.027** on a 0–1 scale.
- The real judge does **worse** than that, at 3× the inference cost.

The fleet is **hierarchical**, not **complementary**. And I could not have known that by
looking at it. I had to measure it.

**The reusable output of this project is not the judge. It's the instrument that told me not
to build one.**

---

## 1. The pattern, and its unexamined precondition

Fan-out + judge is an appealing idea. Three specialists, three answers, a meta-reasoner picks
the best. It shows up everywhere — mixture-of-agents, LLM-as-judge ensembles, debate.

It has a precondition that is almost never stated:

> **Fan-out + judge only pays when the candidates are *comparably strong* and *genuinely
> decorrelated*** — so that different models win on *different inputs*.

If one model is simply better, the judge's best possible move is "always pick that one," and
you've paid 3× inference plus a judge call to reproduce a single API call.

That precondition is a property of the **fleet**, not the judge. **It is testable before you
write a single line of judge code, offline, for $0.** So I wrote the test.

## 2. The metric: HEADROOM

```
HEADROOM  =  ORACLE  −  BEST SINGLE MODEL
```

- **ORACLE** = a *perfect* judge. For every query, it picks whichever candidate actually
  scored highest. No real judge can beat it.
- **BEST SINGLE MODEL** = just always call the strongest specialist. No fan-out, no judge, 1×
  cost.

Headroom is **the entire value the ensemble+judge pattern can *ever* buy on this fleet.** A
real judge captures some fraction of it — possibly a *negative* fraction, since a bad judge is
worse than not judging.

```
headroom ≈ 0    → STOP. No judge, however good, beats one model here. Fix the fleet.
headroom large  → the models disagree usefully. A judge has something to arbitrate.
```

## 3. The first measurement said... nothing. (This is the important part.)

The fleet: a 14B coder, a 7B reasoner, a 9B general model — co-resident on one 48GB GPU,
behind an OpenAI-compatible gateway.

36 labeled queries, graded 0–5 against reference answers by a frontier model.

| policy | score |
|---|---|
| ORACLE — a *perfect* judge, best-of-3 | 0.961 |
| **Always call the strongest model. No judge.** | **0.933** |
| The real judged ensemble | 0.883 |

**Headroom: +0.028.** Even a perfect judge buys under three points. The real judge lands
*below* the single model.

Case closed?

**No.** And catching why is the only reason this write-up is worth reading.

### The instrument was saturated

**31 of the 36 queries had the top candidate already scoring at the grader's maximum.** Where
the best answer is already 5/5, headroom is **zero by construction** — the grader cannot go
higher. The entire +0.028 was earned on **five queries.**

I had measured a fleet with a ruler that was pinned at its maximum. The verdict wasn't
*negative* — it was **undecidable.** The confidence interval was [+0.003, +0.052], and the
"not worth it" threshold (0.05) sat *inside* it. "Not worth it" and "marginally worth it" were
not distinguishable with that data.

**A saturated benchmark doesn't produce a wrong answer. It produces a confident one.** That's
worse.

### Three claims I had to retract

While building the instrument, I found that my own earlier results were wrong. In the interest
of showing the work rather than the highlight reel:

1. ❌ **"The 14B coder is the best candidate on 31 of 36 queries."**
   **A config-ordering artifact.** `max()` returns the *first* maximum, and my candidate list
   started with the coder — so all 28 *tied* queries were silently credited to it. Reverse the
   list and the same data says the general model wins 27. On **strict** (unique) wins it was
   coder 4 / general 4 / reasoning 0.
   **Ties are not wins.** The fix — count only strict winners, and make the count invariant to
   list order — is now a permanent test.

2. ❌ **"The trap questions discriminate 2.6× better."**
   Not significant (Fisher *p* = 0.146). And the traps weren't traps: **every specialist
   answered every one correctly.**

3. ❌ **"The error bar is ±0.010."**
   That was grader *replication* noise (33 of 36 items had a standard deviation of exactly
   zero — the grader just repeated itself). The statistic that actually bounds the claim is the
   **paired SEM across queries: 0.037.** The real floor was ~7× larger than the one I'd
   published.

Each of these made the result look *more* decisive than it was. That's the direction errors
tend to run when you're hoping for a clean story.

## 4. The fix: harder queries, pre-registered

To un-pin the grader I wrote **30 new, harder queries** — ones where even the *best* candidate
should leave points on the table. The mechanism: the old references were single-point facts
(`$0.05`; `3 minutes`), so a model that lands the point scores 5/5 and saturates. Each new
reference instead enumerates **several independently checkable components** — a mechanism, a
precise value, an edge case, a common wrong answer to reject — so partial credit becomes the
norm.

**The queries were written and frozen BEFORE any model answered them.** This matters more than
anything else in this document:

> If you tune queries *after* seeing which model wins, you are selecting for disagreement and
> **manufacturing the very headroom you're trying to measure.** The number becomes meaningless.

I pre-registered them. Not one was re-worded after seeing a result. (Two of my *gold reference
answers* turned out to be factually wrong — `functools.lru_cache` is not built on
`OrderedDict`, and RFC 9110 defines *four* safe HTTP methods, not three. Both would have marked
**correct** model answers down. I fixed those before generating anything, which is legitimate:
correcting an instrument before use is not the same as tuning it toward a result.)

## 5. The result

| | easy (n=36) | **hard (n=30)** |
|---|---|---|
| queries **at the grader's ceiling** | 31/36 (**86%**) | **6/30 (20%)** |
| best single model | 0.933 | **0.696** |
| oracle (a *perfect* judge) | 0.961 | 0.722 |
| **HEADROOM** | **+0.028** | **+0.027** |
| 95% CI | [0.003, 0.052] | [0.004, 0.050] |
| **verdict statistically resolved?** | **NO** | **YES** |
| queries where models **disagree** | 67% | **80%** |
| queries that are an exact **tie** | 78% | 40% |

**The hard queries did exactly their job.** The ceiling collapsed from 86% to 20%. Scores fell
from 0.933 to 0.696 — real room to differentiate. Ties fell from 78% to 40%. The specialists
now **visibly disagree on 80% of queries.**

**And the headroom did not move.** +0.028 → +0.027.

The verdict is now **statistically resolved**, on an instrument that isn't saturated:
**no judge, however perfect, can beat just calling the best single model on this fleet.**

## 6. Why disagreement tripled but the ensemble's value didn't

This is the finding.

> ## **Divergence is not headroom.**

The fleet turned out **hierarchical**:

```
coder  0.696     general  0.527     reasoning  0.518
strict wins:  coder 12/30 · reasoning 4/30 · general 2/30
coder is SIGNIFICANTLY the best member (margin CI [+0.084, +0.254])
```

The models argue constantly. **But when they argue, the coder is usually the one that's
right.** So a perfect oracle barely pulls away from "always call the coder," and a real judge —
which must *also* be right about who's right — does worse.

**Hierarchy is the default, not a quirk.** Any fleet with one genuinely stronger member behaves
this way. A 14B coder simply *is* better than a 7B reasoner and a 9B general model at most
tasks. You need models that are **comparably strong** *and* **win on different inputs** — and
that is a much rarer property than "the models sometimes disagree."

**A fleet can disagree loudly and still be worthless to ensemble. You cannot tell by looking.**

### And "just route instead" is not the escape hatch

The obvious retreat is: *fine, don't judge — **route**. Pick the right model per query, 1×
cost.*

**The headroom bounds routers too.** The oracle is perfect **per-query selection**. A judge
selects *after seeing the candidate answers*; a router selects seeing only the *query*, so it
carries strictly **less** information:

```
router  ≤  judge  ≤  oracle  =  best single + headroom
```

A *perfect* router also buys at most +0.027 here. Headroom doesn't merely condemn the judge —
**it condemns every selection policy over this fleet.**

The honest conclusion isn't "route, don't judge." It's: **just call the strongest model.**
Routing is only the *cheaper way to chase a prize that isn't there.*

## 7. What this does NOT show

**It does not falsify fan-out + judge.** It shows **one fleet failing the pattern's
precondition.**

And I have to name the obvious objection, because it's a good one:

> **This fleet was never *chosen* for complementarity.**

It was chosen by constraints. An 8-vCPU cloud quota capped me at a single GPU → three models
had to be co-resident on 48GB → a **14B** coder instead of the 32B I wanted. And a tokenizer
bug in a Llama-lineage distill (it leaked raw BPE byte markers) forced me to a **second Qwen**
model — so two of my three specialists shared a lineage. Decorrelation was dented **by
accident, not by design.**

So the generalization — *"hierarchy is the default for small mixed fleets"* — is **suggested,
not established.** The instrument claim (*divergence ≠ headroom; measure the precondition
first*) stands regardless, because it's true of any fleet.

### The experiment that settles it

Build a fleet **deliberately selected for complementarity** — comparable strength, genuinely
different pretraining lineages, no obvious dominant member — and re-run the same instrument on
the same pre-registered hard queries.

**Both outcomes are informative, which is what makes it worth running:**

- **Headroom appears** → the pattern is vindicated. My fleet was the problem, and the judge
  becomes worth building. I'll say so.
- **Headroom stays ~0** → *"hierarchy is the default"* is confirmed on a fleet chosen **for**
  decorrelation, which pre-empts the only serious objection to this write-up. That's a much
  stronger claim than I can make today.

I'll pre-register the fleet and publish either result.

## 8. The actual takeaway

If you are about to build a judge, an ensemble, a router, or any meta-reasoner over multiple
models:

> **Measure the headroom first. It's free, it's offline, and it takes an afternoon.**
>
> If the oracle doesn't pull meaningfully away from your best single model, **there is no prize
> to capture**, and no amount of clever judging or routing will conjure one.

The tool is `orchestrator/divergence.py`. It reports headroom with a confidence interval,
strict win counts (ties belong to nobody), the per-query spread, and — the thing that saved
this project from publishing a confident non-result — **how much of your query set is pinned at
the grader's ceiling.**

Point it at your fleet before you build anything for it.

---

### Reproducing this

Everything below runs from a clean clone with **no GPU and no API key, for $0.** Candidates,
judgments and grades are frozen in `orchestrator/eval_fixtures/`.

```sh
python3 orchestrator/divergence.py                          # easy set: +0.028, 31/36 AT CEILING
CONCLAVE_QUERYSET=hard python3 orchestrator/divergence.py   # hard set: +0.027, 6/30 at ceiling
python3 orchestrator/judge_eval.py --score                  # the judge-vs-judge run
```

**Fleet:** Qwen2.5-Coder-14B-AWQ · DeepSeek-R1-Distill-Qwen-7B-FP8 · Gemma-2-9B-AWQ-INT4,
co-resident on one L40S/H100 under vLLM, behind a LiteLLM gateway.
**Grader:** claude-sonnet-5, reference-anchored, 3 samples per answer. Neutral to all three
candidates (two Qwen, one Google — no shared house).

**Known limits, stated plainly:** n=30. Single grader family. The "resolved" verdict clears its
threshold by 0.0002 — read it as *"not worth it, at the boundary,"* not a landslide. Both known
biases (winner's curse on the oracle, in-sample selection of the best single model) inflate
headroom **in the ensemble's favour**, so the true value is if anything **lower**.
