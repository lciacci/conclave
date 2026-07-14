# Disagreement is cheap. Complementarity is rare.

> ## 🔴 RETRACTION (2026-07-14, from code review) — DO NOT QUOTE THE SELF-MoA GAIN
>
> **The "+0.058 / the pattern PAYS" result below is VOID.** Three defects, any one fatal:
> 1. The **baseline was graded at `GRADER_SAMPLES=3`** while the Self-MoA arms were graded at
>    **`=1`** — a single noisy grade compared against a mean-of-three. On a matched baseline the
>    gain is **+0.047, CI [−0.005, +0.099] — it crosses zero.**
> 2. **The judge WAS the grader** (`claude-sonnet-5` chose the answer, then graded its own
>    choice). The run exported only `GRADER_*`; `JUDGE_*` silently fell back to it.
> 3. The **guard written to catch (2) was dead code.**
>
> **"The verdict is RESOLVED" is also retracted** — it used z=1.96 with an estimated sigma on
> data that is 24/30 exact zeros. With the correct t quantile the 0.05 threshold falls *inside*
> the CI on **both** query sets. Nothing is resolved.
>
> **"ORACLE@8 beats the fleet by +0.091" is restated as +0.034** — the rest was max-over-8 vs
> max-over-3 (more lottery tickets) plus the same grading mismatch. Direction survives;
> magnitude was overstated 2.6×.
>
> **What survives:** the ceiling collapse (86%→20%), headroom unchanged (+0.027), disagreement
> tripling while headroom stayed flat, and — robustly, no CI needed — **the fleet is
> hierarchical: the coder wins all three categories, beating the reasoner AT reasoning and the
> general model AT general.**
>
> All defects are fixed in code and verified by execution. The Self-MoA gain is **unmeasured**,
> not positive; a defensible number needs the in-fleet (non-grader) judge.



### What I learned trying to build a judge for a multi-model ensemble — and why I didn't build it

> ## ⚠️ READ THIS FIRST — THIS IS NOT A NOVEL RESULT, AND IT IS NOT FOR PUBLICATION
>
> This is an **internal engineering record**, not a paper. After writing it I went looking for
> prior art and found that **the method here is already published, and our number replicates
> theirs.**
>
> **[arXiv 2606.27288 — "When Does Combining Language Models Help? A Co-Failure Ceiling on
> Routing, Voting, and Mixture-of-Agents"](https://arxiv.org/html/2606.27288)** defines β (the
> rate at which *all* models fail the same query), proves **no router, vote, or cascade can
> exceed 1 − β**, and explicitly frames it as a **"$0 certificate" computable from a held-out
> query set *before a router is trained***. That is exactly the instrument built here.
>
> Their measured oracle gap on a saturated mix: **0.044, 95% CI [0.027, 0.062]**.
> **Ours is +0.027 — their lower confidence bound.** We independently reproduced a published
> result without knowing it existed. Good news for correctness; there is no novelty claim to
> make, and none is made.
>
> **Related prior art to cite, not re-derive:**
> - **Oracle router as a theoretical upper bound** — [Shnitzer et al., arXiv 2309.15789](https://arxiv.org/abs/2309.15789)
> - **Model complementarity** — [RouterBench, arXiv 2403.12031](https://arxiv.org/abs/2403.12031):
>   secondary models give unique correct answers on 10–30% of prompts; and *no router
>   significantly beats always picking one model* (this is **Martian's own benchmark**)
> - **A real judge recovers only ~21% of the oracle gap** (pointwise; 61% pairwise) —
>   [arXiv 2603.12520](https://arxiv.org/abs/2603.12520). Applied to our +0.027, a *realistic*
>   judge buys **+0.006 to +0.017**. Our conclusion is **understated**, not overstated.
> - **Self-MoA** — [arXiv 2502.00674](https://arxiv.org/abs/2502.00674): aggregating N samples
>   from the **single best model** beats mixed-model MoA by **6.6 points**, and wins
>   *specifically in the hierarchical regime* — which is exactly the regime measured here.
>
> **What is kept from this document:** the measurement, the retraction trail, the ceiling
> analysis, and the `specialist_wins_own_category` diagnostic — which I have not seen named in
> the prior art and which is the cheapest way to separate a *fleet* problem from a *query-set*
> problem. It is a lab notebook, and it is useful as one.
>
> **§7 and §8 have been narrowed** — the original scoping was wrong. See the correction there.

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

## 7. What this does NOT show — **the scope correction**

> ### 🔴 THE ORACLE BOUNDS *SELECTION*. IT DOES NOT BOUND *GENERATION*.
>
> This is the most important limitation in the document, and the original draft got it wrong.
>
> Our oracle is `max over 3 candidates, ONE SAMPLE EACH`. That correctly bounds any policy that
> **picks one of those three answers** — a judge in select mode, a router, a majority vote.
>
> It does **NOT** bound anything that **generates new candidates**:
>
> | method | mechanism | bounded by our oracle? |
> |---|---|---|
> | Router / model selection | pick one model's answer | **YES — this is exactly our bound** |
> | Judge in *select* mode | select | **YES** (and strictly below it) |
> | Majority vote over the 3 | select | **YES** |
> | Self-consistency (N samples) | select over **N sampled paths** | **NO** — enlarges the candidate set |
> | Repeated sampling / tree search | **generate** | **NO** — *raises the oracle itself* |
> | Mixture-of-Agents | **synthesize** (output ∉ candidate set) | **NO** |
> | Sakana **Fugu** | delegate + verify + **synthesize** | **NO** |
>
> **And our own judge can synthesize.** The frontier judge set `chosen == -1` on **34 of 36**
> queries — it ignored the candidates and *wrote its own answer*. That is generation, not
> selection, and it is **not** bounded by our oracle. (Empirically it still lost — 0.883 vs the
> 0.933 single model — so the concern is theoretical *here*. But the claim had to be narrowed.)
>
> **[Sakana's Fugu](https://sakana.ai/fugu-release/) (June 2026) beats the best model in its own
> pool on 10 of 11 benchmarks.** A multi-model system *does* beat the best single model on
> quality. It escapes this bound by delegating, **verifying**, and **synthesizing** — producing
> answers that were never in the candidate set. Had this document claimed *"multi-model doesn't
> pay,"* Fugu would refute it in public.
>
> **The delicious detail:** Sakana's own AB-MCTS headline (30% on ARC-AGI-2) is **Pass@250** —
> itself an oracle. Their **Pass@2**, with a *real selector*, is **19.2%**. They lose a third of
> their headline to the selection problem. **This finding appears inside theirs.**
>
> ### So the defensible claim is narrow:
> **Selection over a fixed, hierarchical fleet at one sample per model does not pay.**
> That condemns judges-in-select-mode and routers. It says **nothing** about self-consistency,
> Mixture-of-Agents, search, or synthesis — mechanisms we did not measure, and which the
> literature says *do* pay.

**It does not falsify fan-out + judge.** It shows **one fleet failing the pattern's
precondition** — for **one mechanism** (selection).

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

### But that objection is weaker than it looks — the specialists don't win their own categories

The obvious follow-up is *"your query set was code-heavy, of course the coder won."* **It
wasn't, and it didn't.** The set is a balanced 10/10/10 split, and computing the best single
model **within each category** kills the objection with data already on disk, for $0:

| query domain | best model | the *nominal specialist* |
|---|---|---|
| **coder** queries | coder **0.687** | coder ✓ |
| **reasoning** queries | **coder 0.900** | reasoning — *loses at 0.807* |
| **general** queries | **coder 0.500** | general — *loses at 0.440* |

**The coder wins all three categories** — it beats the reasoner *at reasoning* and the general
model *at general*, on their own turf.

**So the fleet has no specialists.** It has one good model and two worse ones. And the reason is
brutally simple: **at this scale, parameter count beats specialization.** A 14B model is just
better than a 7B and a 9B, *even on their home ground.* "Specialist" was a label, not a
capability. **No reshuffling of the query mix can produce headroom that isn't there.**

That diagnostic (`specialist_wins_own_category`) is now a first-class field in the instrument.
It is the cheapest way to tell a *fleet* problem from a *query-set* problem, and it should be
run on any candidate fleet **before** spending money on it.

### The experiment that actually follows — and it is NOT a new fleet

The literature is ahead of us here, and it points somewhere cheaper and more promising.

**[Self-MoA](https://arxiv.org/abs/2502.00674)**: aggregating **N samples from the single best
model** beats mixed-model MoA by **6.6 points** — and it wins **specifically in the hierarchical
regime**, which is exactly the regime measured above. The quality coefficient dominates the
diversity coefficient. Mixed-model MoA only wins when members are of *comparable strength*.

So the next experiment is not "buy a better fleet." It is:

> **Sample the strongest model N times and synthesize.** This tests the **escape mechanism**
> (generation, not selection), needs **no new fleet**, reuses the same 30 pre-registered
> queries, and the literature *predicts it wins here*.

It also produces the number this document is missing: **the oracle at k > 1 samples per model.**
Our current oracle conflates *"the fleet is hierarchical"* with *"the candidate set has size 3."*
Sampling separates them.

A deliberately decorrelated fleet (comparable **parameter count**, different lineages) remains
worth running eventually — but it is now the *second* experiment, not the first, and it carries a
~50% chance of re-confirming what we already know.

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
