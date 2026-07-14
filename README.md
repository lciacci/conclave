# Conclave

A self-hosted multi-model inference platform on AWS. Three open-weight models
(7B–14B class) serve concurrently on a single GPU behind an OpenAI-compatible
gateway, reachable only over a private WireGuard mesh, with cost controls
wired in before the first GPU ever boots.

The interesting part is not running one model — it's the **judge**: fanning a
query out to several specialized models in parallel and using a meta-reasoner
to select or synthesize the best answer. That part is built and has been
rigorously measured; the headline result is that on *this* fleet, it doesn't
pay off — see [The judge](#the-judge-and-what-it-found) below.

Live descriptor page: **https://houseofyeti.com/conclave/**

## Why

Four goals, in order of honesty:

1. Learn cloud GPU infrastructure and inference serving hands-on.
2. Understand multi-model orchestration deeply enough to have opinions.
3. Manage token/cost at the **model layer**, not the API layer.
4. Operate a real platform end to end — a more distinctive story than "I called
   an API."

> **Honest counter-argument, kept on purpose:** OpenRouter gives multi-model
> access for less money than self-hosted GPUs unless utilization is high. This
> project is justified by *learning the infra* and *stack control*, not cost.

## Architecture

```
  Local devices ──WireGuard (Tailscale)──▶ g6e.xlarge (L40S 48GB)
   (no public ports, ACL-restricted)          │
                                         LiteLLM gateway :4000
                                              │
                          ┌───────────────────┼───────────────────┐
                          ▼                   ▼                   ▼
                    vLLM · coder        vLLM · reasoning     vLLM · general
                 Qwen2.5-Coder-14B   DeepSeek-R1-Distill-   Gemma-2-9B-IT
                                       Qwen-7B (FP8)
                          └───────────────────┴───────────────────┘
                                    EFS model cache
                              (S3 cold archive for inactive weights)
```

- **Compute:** g6e.xlarge — one L40S (48 GB, 864 GB/s). Three 7B–14B-class
  models co-reside on it; each vLLM process claims an additive slice of GPU
  memory (general 0.25 / coder 0.30 / reasoning 0.24), started sequentially so
  each memory check sees real predecessor residency.
- **Serving:** vLLM (continuous batching, paged attention) — chosen over Ollama
  precisely because it exposes the serving internals worth learning.
- **Gateway:** LiteLLM — one OpenAI-compatible API in front of every backend,
  and the seam for per-model cost accounting.
- **Orchestrator:** a client-side Python module (`orchestrator/ensemble.py`)
  fans a request out to the three specialists over that same wire format, then
  routes the answers to a judge — no SDK, stdlib `urllib` only.
- **Network:** Tailscale mesh, zero public ingress. Session Manager for shell
  access — no SSH keys, no port 22.
- **Storage:** EFS for the active model cache (instance-type-agnostic, survives
  teardown), S3 for cold weights, EBS for the OS volume only.

Full reasoning for every locked decision lives in [`docs/design.md`](docs/design.md);
session-to-session state and the full experimental log live in
[`docs/HANDOFF.md`](docs/HANDOFF.md).

## Cost controls (built first, on purpose)

Controls exist **before** the first GPU instance launches. A forgotten weekend
on a running GPU burns roughly the entire monthly cap.

| Control | Mechanism |
|---|---|
| $100/mo budget, tiered alerts | AWS Budgets (50 / 80 / 100%) |
| Hard stop | 100% breach → SNS → Lambda stops every tagged instance |
| Idle stop | CloudWatch alarm (CPU or GPU-util < 5% / 30 min) → native EC2 stop action |
| Anomaly detection | AWS Cost Anomaly Detection, daily email |
| Spot + tagging | Spot where interruption is tolerable; every resource tagged |

The hard-stop kill switch and idle-stop were both verified end-to-end against a
throwaway micro instance before any GPU spend. Nothing is running today — the
platform is torn down between sessions and relaunched on demand
(`terraform apply -var enable_gpu=true`).

## The judge (and what it found)

The arbiter, judge, mediator, critic, and router are one family:
**meta-reasoners over specialized outputs.** They differ in ground truth,
latency budget, and what they optimize. Conclave's judge does *selection /
synthesis* over parallel responses to the same query, on every request — a
distinct problem from a triage arbiter that runs once.

The judge target is a config value (model + URL), not hardcoded, and defaults
to an in-fleet Gemma-9B rather than a frontier model. It was evaluated against
a frontier judge (Claude) on 36 labeled queries, reference-graded:

| | score |
|---|---|
| ORACLE — a *perfect* judge, best-of-3 | 0.961 |
| **Always call the single strongest model, no judge** | **0.933** |
| The judged ensemble (Gemma judge, the v3 design) | 0.883 |

**Headroom** — the entire value a judge can ever add on this fleet, defined as
`oracle − best single model` — measures **+0.028**. Even a perfect judge would
barely beat just always calling the strongest specialist; the real Gemma judge
does *worse* than that (−0.050 vs. always-coder), for 3× the inference cost
plus a judge call plus a measured ~30% GPU-contention tax. Root cause: the
three candidates are **redundant, not hierarchical** — 28 of 36 queries are an
exact tie at the top, so there's little for a judge to arbitrate.

An earlier claim that "the coder model wins 31/36 queries" was **retracted**
— it was a config-ordering artifact (`max()` returns the first maximum, and
the tied queries were all credited to whichever candidate came first in the
list). Reversing the list credits the same ties to a different model.

That first measurement was **ceiling-limited** — 31 of 36 queries scored at the
grader's maximum, where headroom is zero *by construction* — so the verdict was
**undecidable**, not negative. To settle it, a second set of **30 harder queries**
was written and **frozen before any model answered them** (pre-registered, so the
queries couldn't be tuned toward a flattering result), and the fleet was re-measured.

### The result: disagreement is cheap; complementarity is rare

| | easy (n=36) | **hard (n=30)** |
|---|---|---|
| queries at the grader's ceiling | 31/36 (**86%**) | **6/30 (20%)** |
| best single model (coder) | 0.933 | **0.696** |
| oracle (a *perfect* judge) | 0.961 | 0.722 |
| **headroom** | **+0.028** | **+0.027** |
| verdict statistically resolved? | **no** | **yes** |
| queries where models disagree | 67% | **80%** |

The hard queries did their job: the ceiling collapsed, scores fell, and the
specialists went from tying on 78% of queries to disagreeing on 80% of them.
**And the headroom did not move.**

**The ceiling was hiding nothing.** The reason disagreement tripled while the
ensemble's value stayed flat is that **divergence is not headroom**. The fleet is
**hierarchical, not complementary**: coder 0.696 vs. general 0.527 and reasoning
0.518, with coder winning 12 of 30 queries outright and now *significantly* the
strongest member. The models argue constantly — but **when they argue, the coder is
usually the one that's right.** So even a perfect oracle judge barely beats just
always calling the coder, and a real judge does worse.

That generalizes. **Hierarchy is the default**, not a quirk of this fleet: any fleet
with one genuinely stronger member behaves this way, and a 14B coder simply *is*
better than a 7B reasoner and a 9B general model at most tasks. A fleet can disagree
loudly and still be worthless to ensemble — **and you cannot tell by looking.**

### The headroom bounds routers too — so "just route instead" is not the escape

The oracle is perfect **per-query selection**. A judge selects *after seeing the
answers*; a router selects seeing only the *query*, so it has strictly **less**
information:

```
router  ≤  judge  ≤  oracle  =  best single + headroom
```

So a *perfect* router also buys at most **+0.027** on this fleet. Headroom doesn't merely
condemn the judge — **it condemns every *selection* policy over this fleet.**

## But the pattern does pay — from **sampling**, not from a fleet

The oracle bounds **selection**. It does **not** bound **generation** — anything that
produces candidates that weren't in the set escapes it entirely.

So we dropped the two weaker models and sampled the **best** model 8× instead
(temperature 0.8), on the same 30 queries, with the same judge and the same grader.
**The only thing that changed is where the candidates come from:**

| | candidates | judge score | vs. no judge |
|---|---|---|---|
| **Fleet** (the original design) | 3 models × 1 sample | 0.883 | **−0.050** — the judge is *worse than no judge* |
| **Self-MoA** | 1 model × 8 samples | **0.753** | **+0.058** ✅ CI [+0.005, +0.110] |

```
baseline   the model, one sample        0.696
mean       an average temp-0.8 sample   0.695   ← sampling costs nothing
ORACLE@8   the best of 8 samples        0.813   ← the whole fleet's oracle: 0.722
```

**Eight draws from one model reach a ceiling 0.091 above what three different models could
ever reach.** Sampling headroom is **+0.118** against the fleet's **+0.027** — 4.4× larger —
and a real judge captures **49%** of it (inside the 21–61% band the literature predicts).

**The fleet was never the point. The candidate set size was.**

The pattern works. It just doesn't work with a fleet of weaker specialists — it works with
**repeated samples of your best model**. And that's *cheaper* than the fleet design: one
model, `n=8` in a single request (vLLM processes the prompt once and forks 8 continuations
sharing the prefill KV cache), plus a selector. No co-residency, no contention tax, no fleet
to decorrelate.

### Still untested: synthesis

Selection is bounded by that 0.813 oracle. **Synthesis isn't** — its output need not be any
candidate. Testing it honestly requires a judge that is **weaker** than the candidates (so it
has to actually read them) and a grader from a **different house** than the judge. That's the
next experiment, and it's the one place a fleet might still earn its keep.

*(An earlier synthesis run scored a perfect 1.000 and was **void**: the judge ignored the
candidates and wrote its own answer, and the grader was the same model as the judge. The
harness now detects and refuses that.)*

**The reusable output of this phase is the instrument, not the judge.**
`orchestrator/divergence.py` measures headroom, ties, and ceiling saturation for any
fleet or query set — offline, for $0, **before** you build a judge for it.

## Reproducing the eval (no GPU, no API key, $0)

```sh
python3 orchestrator/judge_eval.py --score   # replays the judge-vs-judge run: 0.883 / 1.000
python3 orchestrator/divergence.py --demo    # fleet-headroom instrument's self-checks
python3 orchestrator/ensemble.py             # offline demo of the fan-out + judge pipeline
python3 orchestrator/selfmoa.py --demo       # the sampling (generation) instrument
```

Re-deriving the two headline numbers needs only a grader key (no GPU — every candidate
response is frozen):

```sh
export GRADER_URL=https://api.anthropic.com GRADER_MODEL=claude-sonnet-5 GRADER_API_KEY=...

python3 orchestrator/divergence.py                          # easy set: +0.028, 31/36 AT CEILING
CONCLAVE_QUERYSET=hard python3 orchestrator/divergence.py   # hard set: +0.027,  6/30 at ceiling
                                                            #   ...and: does each specialist even
                                                            #   win its OWN category? (it doesn't)
CONCLAVE_QUERYSET=hard python3 orchestrator/selfmoa.py      # ORACLE@8 = 0.813 vs fleet's 0.722
CONCLAVE_QUERYSET=hard python3 orchestrator/selfmoa_judge.py --mode select   # 0.753 (+0.058)
```

All candidate responses, judgments, and grades are frozen in
`orchestrator/eval_fixtures/`, so these replay from a clean checkout with zero
live calls. Running against a live fleet requires booting the GPU (see
`docs/HANDOFF.md` for the boot playbook) and is not needed to inspect the
result.

## Phases

| Phase | State | Scope |
|---|---|---|
| **v0.5** | done | Cost layer — budget, hard-stop, idle-stop, tagging. Zero GPU spend. |
| **v1** | done | Qwen 2.5 72B AWQ on vLLM, Tailscale-reachable. Verified end-to-end (curl → tokens), then torn down. |
| **v2** | done | LiteLLM gateway in front of 3 co-resident specialists, per-model cost accounting, GPU-util idle-stop. Verified end-to-end. |
| **v3** | built, measured | Ensemble fan-out + judge. Built and verified; the headroom precondition measured at +0.028 and not yet statistically settled — see above. |
| **v4** | maybe | MCP server as the structured front-end to the platform. |

## Stack

Terraform · AWS (EC2 G-instances, EFS, S3, Lambda, Budgets, CloudWatch) ·
vLLM · LiteLLM · Tailscale · Python

## Layout

```
docs/
  design.md                    decision log — every locked call with its reasoning
  HANDOFF.md                   session-to-session state, experimental log, boot playbook
  conclave-overview.html       this repo's public descriptor page
infra/                         Terraform: cost layer + gated GPU/gateway module
  budget.tf                    budget + tiered alerts
  hardstop.tf                  SNS → Lambda kill switch
  idle_stop.tf                 idle CloudWatch alarm
  gpu.tf / efs.tf               GPU instance + model cache (enable_gpu gated)
  user-data.sh.tftpl           first-boot: tailscale join, EFS mount, vLLM + LiteLLM serve
litellm/config.yaml            gateway config reference (live config is generated by user-data)
orchestrator/
  ensemble.py                  fan-out + judge orchestrator (client-side, stdlib only)
  harness.py                   live smoke test + contention baseline against a running fleet
  divergence.py                fleet-headroom instrument (offline, $0)
  judge_eval.py                judge-vs-judge eval harness (generate / frontier / score phases)
  eval_queryset.py             the 36-query labeled eval set
  eval_fixtures/               frozen candidates, judgments, and grades — replay for $0
scripts/
  sweep-gpu-capacity.sh        fast-fail AZ sweep for GPU capacity errors
```

---

Source: https://github.com/lciacci/conclave

Built with [Tessera](https://github.com/lciacci) — a decision-tracking
development framework. Design decisions here are logged as they were made, not
reconstructed after.
