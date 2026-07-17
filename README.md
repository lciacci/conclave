# Conclave

A self-hosted multi-model inference lab. Open-weight models serve behind an
OpenAI-compatible gateway, reachable only over a private Tailscale mesh, with
cost controls wired in before the first GPU ever boots.

The thesis started as *the judge* — fan a query out to several models and use a
meta-reasoner to select or synthesize the best answer. The project rigorously
measured that idea and **disproved it**: on every fleet built, including a
deliberately ideal modern one, a judge does not pay. The finding that survived
is **route, don't judge** — and the real deliverable is the **instrument** that
tells you which, for any fleet, before you build anything.

With the thesis settled, the project has **pivoted to practical use: run real project and
agentic work on the owned model, local-first.** A $0 4-bit Qwen3-Coder-30B on a 64 GB laptop
(Ollama) scored close to a rented FP8 80B on coding *knowledge* questions (0.900 vs 0.949) — an
underpowered result (n=30) where, on the queries that resolved, the 80B still led — so the **local
model is the daily driver on cost** (free, on-laptop), with the hosted GPU escalated for the harder
fraction. Its competence on real agentic coding is still to be proven in daily use. In the
wider picture Conclave is the **substrate** (serving + the measurement instrument) alongside two
sibling projects — **Tessera** (governance + routing policy) and **pr-arbiter** (a union-recall
review pattern); the cohesion between them is mapped in `docs/INTEGRATION.md`.

Live descriptor page: **https://houseofyeti.com/conclave/**

## Why

Four goals, in order of honesty:

1. Learn cloud GPU infrastructure and inference serving hands-on.
2. Understand multi-model orchestration deeply enough to have opinions.
3. Manage token/cost at the **model layer**, not the API layer.
4. Operate a real platform end to end — a more distinctive story than "I called
   an API."

> **Honest counter-argument, kept on purpose:** a serverless multi-model API
> (OpenRouter, Together) is cheaper than self-hosted GPUs unless utilization is
> high. This project is justified by *learning the infra* and *privacy/stack
> control* — the fleet is self-hosted and reachable only over a private mesh, on
> purpose — not by cost.

## The current fleet

Three **comparable-strength, genuinely different-lineage** open models, one per
48 GB GPU, served privately over Tailscale:

```
  Laptop ──Tailscale (userspace, no public ports)──▶ RunPod pod (3× L40S 48GB)
                                                       │
                         ┌─────────────────────────────┼─────────────────────────┐
                         ▼                             ▼                           ▼
                   vLLM · qwen3                  vLLM · gemma3               vLLM · mistral
              Qwen3-32B-AWQ (Alibaba)     Gemma-3-27B-FP8 (Google)   Mistral-3.2-24B-FP8 (Mistral)
                    GPU 0                        GPU 1                       GPU 2
```

- **Compute:** rented per-session on RunPod, one model per L40S card (no
  co-residency, no GPU-contention tax). The fleet was originally single-GPU on
  AWS g6e; capacity exhaustion moved it to RunPod, and the AWS path (cost layer +
  Terraform) remains a documented fallback.
- **Serving:** vLLM per model, pinned via `CUDA_VISIBLE_DEVICES`. `runpod/boot.sh`
  is fail-closed (proves the pod can stop itself before loading weights) and
  driver-aware (CUDA ≥ 13 → vLLM 0.24; 12.x → vLLM 0.11) — the GPU driver is a
  per-machine lottery, not a per-GPU-type property.
- **Network:** Tailscale mesh in userspace mode (`tailscale serve`), zero public
  ingress. Everything is reachable by MagicDNS name from the laptop only.
- **Orchestrator:** client-side Python, stdlib `urllib` only — no SDK.

The original three-lineage rationale (a judge feeds on *decorrelated* failure
modes) still holds; what changed is the models are now current-gen (~24–32B) and
of comparable strength, which is exactly the regime where an ensemble *should*
pay if it ever does.

## What it found

The core question — *does fan-out + judge pay?* — was measured on **three** fleets
(old L40S; an ideal peer-modern one; a genuine-specialist one). The answer is **no**
on all three, and the specialist fleet is the strongest version of that result.

### The instrument: headroom

`orchestrator/divergence.py` measures **headroom = oracle − best single model** —
the entire value any selection policy (judge or router) can *ever* add on a fleet.
It runs offline, for $0, on frozen candidates, **before** you build a judge.

### The ideal modern fleet, measured

Qwen3-32B / Gemma-3-27B / Mistral-3.2-24B, graded by a neutral third house
(gpt-5.2), 30 pre-registered hard queries:

| | score |
|---|---|
| qwen3 (best single) | 0.935 |
| gemma3 | 0.911 |
| mistral | 0.907 |
| ORACLE (a *perfect* judge) | 0.976 |
| **headroom** | **+0.040** (CI [+0.012, +0.068]) |
| in-fleet judge (gemma3, select mode) | **0.920 — *below* best single** |

The three models are within 0.028 (statistically tied) and each wins a different
query category — textbook complementarity, the ideal ensemble regime. **And a
self-hosted judge still loses to just always calling the strongest model.**
Making the fleet *better* (fixing qwen3) *lowered* the headroom — the
**convergence** effect: as strong models agree more, a perfect judge beats the
best single by less. Route, don't judge, confirmed on the good fleet.

### Pairwise on the peer fleet: a router signal appears (but see below)

Absolute grading saturates — 20/30 queries had every strong answer at the
grader's max, so it couldn't tell which was better. **Pairwise** grading (blinded,
both orders, position-debiased) breaks those ties:

```
per-query winners:  mistral 10 · qwen3 6 · gemma3 6
on the 20 "ties":   SPLIT across all three (top model only 50%)
```

On the peer fleet the absolute headroom **understated** a routing signal — the
"ties" hid real per-query variation. But that signal is fleet-dependent, and it
did not survive two follow-ups.

### The genuine-specialist fleet: the signal vanishes

Built a fleet of real specialists of different kinds — Qwen3-Coder-Next-80B /
DeepSeek-R1-32B / Llama-3.3-70B — to ask whether genuine specialization produces
the divergence the peer generalists lacked. It did the **opposite**:

```
headroom  +0.0244  (LOWER than the peer fleet's +0.040)
per-category:  the 80B coder wins ALL THREE, beating each specialist on its own turf
pairwise:  coder 18/30 wins · 100% of the tie-breaks → CONCENTRATED (peer fleet SPLIT)
```

The specialist fleet is the **most hierarchical** of the three: the biggest model
dominates every axis, so route to it — judge, ensemble, *and* router all lose.
(A query-only category router was also measured directly and captures ~nothing
over "always call the strongest.") Honest caveat: the 80B coder simply outclasses
the 32B reasoner and quantized 70B general — which is itself the finding, on
genuinely different architectures: **quality/parameter count beats specialization,
and matched-strength open specialists may not exist yet (convergence).**

### Where it landed

- **A judge picks *after* generating N answers** — pays N× for a small, saturated
  gap. Disproved on three fleets.
- **Route to the strongest model.** A query-only router only pays when pairwise
  winners *split* (peer fleet, weakly) — the specialist fleet concentrates, so no
  router. **The diagnostic tells you which case a fleet is in.**
- **The reusable deliverable is the instrument:** `divergence.py` /
  `fleet_pairwise.py` correctly said "don't ensemble" on all three fleets, for $0,
  before any judge was built. Judging is **parked with a trigger** — and the
  specialist fleet was the strongest test of that trigger: genuine specialists
  still didn't diverge, they concentrated on the biggest model.

*(One mechanism did pay on the earlier fleet: **Self-MoA** — sampling the single
best model N times and selecting, +0.071 over baseline. It's a generation trick,
not a fleet trick, and its production shape is `n=8` in one vLLM request. Untested
on the modern fleet.)*

## Cost controls (built first, on purpose)

Controls exist **before** any GPU launches. A forgotten weekend on a running GPU
burns roughly the entire monthly cap.

- **AWS path:** Budgets with tiered alerts, an SNS→Lambda hard-stop kill switch,
  and a CloudWatch idle-stop — all verified end-to-end against a throwaway micro
  instance before any GPU spend.
- **RunPod path:** the prepaid credit balance is the absolute out-of-band cap; a
  `watchdog.sh` enforces a hard TTL and an idle-stop *gated on fleet-ready* (so
  weight download isn't mistaken for idle); `boot.sh` fails closed. RunPod has no
  out-of-band stopper — everything runs on the pod — so the credit balance is
  kept small on purpose.

Nothing runs between sessions — the fleet is torn down and relaunched on demand.

## Reproducing the eval (no GPU, $0)

Every candidate, judgment, and grade is frozen in `orchestrator/eval_fixtures/`,
so the results replay from a clean checkout with zero live calls:

```sh
python3 orchestrator/divergence.py --demo         # headroom instrument self-checks
python3 orchestrator/selfmoa_judge.py --demo      # judge guards self-check

# the modern-fleet result (grader key only, no GPU — candidates are frozen):
export GRADER_URL=https://api.openai.com GRADER_MODEL=gpt-5.2-2025-12-11 GRADER_API_KEY=...
MODERN_FLEET=modern2 CONCLAVE_QUERYSET=hard python3 orchestrator/divergence_modern.py   # +0.040
MODERN_FLEET=modern2 CONCLAVE_QUERYSET=hard python3 orchestrator/fleet_pairwise.py       # the router signal
```

## Phases

| Phase | State | Scope |
|---|---|---|
| **v0.5** | done | Cost layer — budget, hard-stop, idle-stop, tagging. Zero GPU spend. |
| **v1** | done | Qwen 2.5 72B AWQ on vLLM, Tailscale-reachable. Verified, torn down. |
| **v2** | done | LiteLLM gateway + 3 co-resident specialists, per-model cost accounting. |
| **v3** | done, measured | Ensemble + judge. Built, then **disproved**: judge does not pay, on the old fleet *or* the ideal modern one. Route, don't judge. |
| **modern fleet** | done | Qwen3-32B / Gemma-3-27B / Mistral-3.2-24B, private over Tailscale on RunPod. Headroom +0.040; pairwise shows a real routing signal. |
| **router** | next | Query-only picker; measure whether it beats "always the strongest" — how much of the pairwise oracle ceiling a *predictable* router captures. |

## Stack

Terraform · AWS (cost layer, fallback) · RunPod (current fleet) · vLLM · LiteLLM ·
Tailscale · Python (stdlib only in the orchestrator)

## Layout

```
docs/
  design.md                    decision log — every locked call with its reasoning
  HANDOFF.md                   session-to-session state, experimental log, boot playbook
  conclave-overview.html       public descriptor page
infra/                         Terraform: cost layer + gated GPU/gateway module (AWS fallback)
runpod/                        the current fleet: boot.sh (fail-closed), watchdog.sh, fleet configs
orchestrator/
  divergence.py                fleet-headroom instrument (offline, $0) — THE reusable output
  divergence_modern.py         headroom for the modern fleet
  fleet_pairwise.py            saturation-free pairwise grading — the router's training signal
  selfmoa.py / selfmoa_judge.py   the sampling (generation) instruments
  judge_eval.py                judge-vs-judge eval harness
  eval_queryset.py / eval_queryset_hard.py   labeled eval sets (36 easy, 30 pre-registered hard)
  eval_fixtures/               frozen candidates, judgments, grades — replay for $0
```

---

Source: https://github.com/lciacci/conclave

Built with [Tessera](https://github.com/lciacci) — a decision-tracking
development framework. Design decisions here are logged as they were made, not
reconstructed after.
