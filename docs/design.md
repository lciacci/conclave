# Conclave — Design

Multi-model inference lab on AWS. Self-hosted open-weight models behind a LiteLLM gateway,
Tailscale-only access, ensemble + judge orchestration as the pedagogical core.

Decision log below. Locked decisions carry their reasoning — reopen only if the stated
assumptions break.

## Why this exists

1. Playground for cloud GPU infra and inference serving.
2. Road to deeper understanding of multi-model orchestration.
3. Hands-on token/cost management at the model layer (not the API layer).
4. Job-search story: "architected and operated a multi-model inference platform with cost
   controls" — more distinctive than "I used the OpenAI API."

**Honest counter-argument (kept on purpose):** OpenRouter gives multi-model access cheaper than
self-hosted GPUs unless utilization is high. Justification is learning the infra and stack
control, not cost. Re-check utilization honestly before scaling spend.

## Locked decisions

### Compute — v1: g6e.xlarge (1× L40S, 48 GB VRAM)

- 70B at 4-bit (AWQ/GPTQ) ≈ 40 GB → fits a single L40S. Hits the 70B-class goal at
  ~$1.86/hr on-demand vs ~$5.67/hr for g5.12xlarge (verify current prices/spot at launch).
- Memory bandwidth (inference is bandwidth-bound): L40S 864 GB/s > A10G 600 GB/s > L4 300 GB/s.
  Single-GPU serving beats 4× A10G tensor-parallel for one model — no inter-GPU sync.
- **g6 (L4) rejected**: half the bandwidth of A10G. Newer ≠ faster for inference.
- **g5.12xlarge deferred to v2/v3**: earns its cost only when multiple models are resident
  simultaneously. Revisit when ensemble work starts; g6e.12xlarge (4× L40S) is the alternative
  to price-compare at that point.

### Inference backend — vLLM from v1

- Ollama rejected for v1: it hides exactly what this project exists to learn (continuous
  batching, paged attention, quantization configs, serving metrics).
- LiteLLM abstracts the backend, so this is not lock-in; migration cost either direction is low.
- Setup cost ≈ half a day. Accepted.

### Storage — EFS (active cache) + S3 (cold archive) + EBS (OS only)

Locked before this doc; reasoning preserved:

- EFS mounts to any instance type in the VPC — instance-type-agnostic, survives teardown.
- S3 for inactive weights (~$15/mo for 5 stored 70B models vs ~$200/mo on EFS).
- EBS wins only if: single fixed instance type, fine-tuning with hot checkpoint writes, or cold
  load latency matters operationally. None fit this project. Models load to VRAM once; EFS's
  slower I/O costs only on infrequent cold starts.
- Revisit if shape shifts toward single-instance production-style serving.

### Networking — Tailscale mesh, zero public ports

- Local devices reach the instance like LAN, everything over WireGuard.
- Tailscale ACLs restrict which devices reach the inference box.
- Non-negotiable: no public ingress on the instance, ever.

### Gateway — LiteLLM

OpenAI-compatible proxy in front of all backends. Everything local speaks one API regardless of
what's serving behind it. Also the natural seam for per-model token/cost accounting (purpose #3).

### Platform — Terraform, us-east-1, dedicated AWS account

- **Terraform** for all infra: free teardown/rebuild, tracked state, and the IaC vocabulary the
  platform story expects. Bash+CLI rejected — no state, no clean destroy.
- **us-east-1**: g6e available, deepest spot pools, marginally cheapest. Latency irrelevant —
  inference rides Tailscale, +40 ms is nothing.
- **Fresh AWS account, dedicated to this project** (decided 2026-07-02, no prior account
  existed): blast-radius isolation, bill is the project's bill — no tag-filtering needed to
  answer "what does conclave cost."

## Judge architecture (v3 — the thesis)

> ### 🔴 THE PRECONDITION (measured 2026-07-12): this pattern needs a fleet with HEADROOM.
>
> Fan-out + judge only pays when the candidates are **comparably strong** and **genuinely
> decorrelated** — so different models win on different inputs. That is a property of the
> **fleet**, and it is measurable *before* any judge is built, offline, for $0:
>
> **HEADROOM = ORACLE (a perfect judge) − BEST SINGLE MODEL** — the entire value this pattern
> can *ever* buy on a given fleet. A real judge captures a fraction of it; a bad one captures a
> negative fraction.
>
> | policy | score (n=36) | cost |
> |---|---|---|
> | ORACLE — a *perfect* judge, best-of-3 | 0.961 | 3× inference + perfect judgment |
> | **ALWAYS coder — one model, no judge** | **0.933** | **1× inference** |
> | Gemma-judged ensemble (this design) | 0.883 | 3× inference + judge + ~30% contention |
>
> **Conclave's fleet: headroom = +0.028** (95% CI [+0.003, +0.052]) — so even a *perfect* judge
> beats one model by under 3 points, and the real judge lands **−0.050** below it. **The
> candidates are REDUNDANT, not hierarchical: 28/36 queries are an exact TIE**; strict (unique)
> wins are **coder 4 / general 4 / reasoning 0**, and no model is significantly the best.
>
> ⚠️ **CEILING-LIMITED, and the verdict is NOT SETTLED.** 31/36 queries have the top candidate
> already at the grader's maximum, where headroom is 0 *by construction* — the whole result rests
> on **5 queries**. The 0.05 decision threshold lies *inside* the CI, so "not worth it" vs
> "marginal" is **not distinguishable at n=36**. **The next step is HARDER QUERIES, not (yet) a
> different fleet:** you cannot diagnose a fleet with an instrument pinned at its maximum.
>
> ❌ **RETRACTED:** "the 14B coder is the best candidate on 31/36 queries" — a **config-ordering
> artifact** (`max()` takes the first max; reverse the candidate list and `general` wins 27).
>
> **This does not falsify the pattern** — multi-model systems work in production. It says this
> *instantiation* fails the precondition, and it hands you the instrument to check the next one:
> `orchestrator/divergence.py`. **Vet any candidate fleet for headroom before building a judge
> for it.** Note also what production multi-model systems mostly do: **route**, not fan-out-and-
> vote. The "cheap sibling" named in the next paragraph now looks like the better bet on a fleet
> this skewed, and "always pick the strongest model" is the baseline any future design must beat.
> See `docs/chunk3-judge-eval-results.md`.
>
> ---
>
> ## ✅ SETTLED 2026-07-14 (n=30 pre-registered HARD queries). **Disagreement is cheap; COMPLEMENTARITY is rare.**
>
> The verdict above was **undecidable**, not negative: 31/36 of its queries were pinned at the
> grader's ceiling, where headroom is 0 *by construction*. A second set of 30 harder queries was
> written and **frozen before any model answered them**, and the fleet was re-measured.
>
> | | easy (n=36) | **hard (n=30)** |
> |---|---|---|
> | at the grader's ceiling | 31/36 (**86%**) | **6/30 (20%)** |
> | best single (coder) | 0.933 | **0.696** |
> | oracle (a *perfect* judge) | 0.961 | 0.722 |
> | **HEADROOM** | **+0.028** | **+0.027** ← unchanged |
> | verdict resolved? | **NO** | **YES** |
> | models disagree | 67% | **80%** |
>
> **The ceiling was hiding nothing.** Removing it TRIPLED the disagreement and moved the answer
> not at all — because **divergence is NOT headroom.** The fleet is **HIERARCHICAL, not
> complementary**: `coder 0.696 / general 0.527 / reasoning 0.518`; strict wins coder **12/30**;
> coder is now *significantly* the best member (margin CI [+0.084, +0.254] — on the easy set it
> was **not**). **The models disagree constantly — but when they disagree, CODER IS USUALLY
> RIGHT.** So even a *perfect* judge barely beats always calling coder, and a real one does worse.
>
> **Hierarchy is the DEFAULT, not a quirk of this fleet.** Any fleet with one genuinely stronger
> member behaves this way, and a 14B coder simply *is* better than a 7B reasoner and a 9B general
> model at most tasks. **A fleet can disagree loudly and still be worthless to ensemble — and you
> cannot tell by looking.** That is precisely why the instrument runs BEFORE the judge.
>
> **The bar a future fleet must clear:** comparable strength, genuinely different lineages, models
> that win on *different inputs* — and it must beat a **router** costing 1× inference rather than
> 3× + a judge + the measured ~30% contention tax. That is a high bar, and it is why production
> multi-model systems mostly route.
>
> **Caveat, stated:** the CI upper bound is 0.0498 against a 0.05 threshold — resolved by 0.0002.
> Razor-thin. But both known biases (winner's curse on the oracle; in-sample pick of best-single)
> inflate headroom *in the ensemble's favour*, so the true value is if anything **lower**.
>
> **A confound worth naming:** the AWS 8-vCPU quota **chose this fleet**, not the designer — it
> forced one GPU, hence co-residency, hence a 14B coder instead of 32B; and the Llama distill's
> byte-marker leak forced a *second Qwen*, denting lineage decorrelation. So this fleet was never
> selected for the property the pattern needs. **That is context, not an excuse** — the models
> did diverge on 80% of queries; they just diverged *hierarchically*.

Fan out a query to N models in parallel; a judge model selects the best response or synthesizes.
Routing by task type is the cheap sibling (router picks one specialist; no fan-out cost).

**Fork of the #9 arbiter, not shared code.** Arbiter = triage over opinionated findings, runs
once. Judge = selection/synthesis over parallel responses, runs per-request. Different ground
truth, latency budget, optimization target. Inherit the lessons — prompting meta-reasoners
without splitting the difference, eval'ing the meta-reasoner separately from specialists,
handling ties — share learnings, not code.

**Pattern family framing:** arbiter, judge, mediator (resolves agent disagreement), critic
(scores outputs), router (selects specialist) are all *meta-reasoners over specialized outputs*.
Naming the family is what turns two similar-looking components into a design-pattern
exploration. This framing feeds project #10 and the job-search narrative.

### v3 locked decisions (2026-07-08)

Four decisions, resolved before writing code. All keep the expensive GPU out of the dev loop and
the frontier out of the critical path.

1. **Orchestrator placement — client-side.** The orchestrator (fan-out → collect → judge →
   return) is new code regardless; LiteLLM has no native fan-out. It runs on the local machine
   (or any Tailscale client), calling the gateway. Rationale: iterate judge logic with the GPU box
   *down* against recorded/cached candidate responses — pay for the GPU only when running a real
   ensemble. The Tailscale hop adds a known-constant latency, subtractable from measurements.
   *Deferred:* hosting the orchestrator so it runs "from any device" without a local process — a
   later problem, and the API shape (decision 3) already makes any client a first-class caller.

2. **Judge model — pluggable, default in-fleet (Gemma-9b).** No co-resident 4th model fits (fleet
   is at 0.86 util). So the judge target is a *config value* (model name + base_url), not
   hardcoded. Default is the in-fleet **general/Gemma-9b** — chosen over the in-fleet reasoning-7B
   because Gemma's lineage is decorrelated from the two Qwen candidates (coder + reasoning), so it
   is less likely to share their blind spots. Toggling the judge to an external frontier model is
   a one-line config change. This *is* the "eval the meta-reasoner separately" experiment: run the
   identical ensemble with `judge=gemma` vs `judge=frontier` and compare selection quality. The
   frontier judge is a **baseline to beat**, never the default — the standing goal is no reliance
   on frontier offerings in the critical path.

3. **Fan-out API — OpenAI-compatible virtual model + debug metadata.** Expose a virtual
   `model=ensemble` on the gateway; a normal `/v1/chat/completions` call returns the judged answer.
   The N candidate responses + judge rationale ride in a response-metadata field for eval/debug.
   Note: "OpenAI-compatible" is a *wire format* (the `/v1/chat/completions` schema), not a
   dependency on OpenAI — the whole stack already speaks it (vLLM serves it, LiteLLM routes it) and
   calls zero OpenAI servers. Choosing it means any OpenAI-SDK client, MCP server (v4), or curl can
   drive the *self-hosted* gateway; it increases independence. Beats a bespoke endpoint on
   drop-in compatibility + reuse of existing per-model cost accounting.

4. **Latency realism — single-GPU contention baseline first, multi-GPU as a measured follow-on.**
   The orchestrator/judge/API/eval harness are identical on 1 GPU or 4, so build them on the cheap
   box; going multi-GPU then changes *only* topology (Terraform), not app code — de-risking the
   expensive phase. First fan out to the 3 co-resident models and quantify the SM-contention
   serialization penalty ("co-resident fan-out costs ~X% vs ideal parallel") — that number is both
   a deliverable and the evidence that *justifies* multi-GPU. **Defined precisely (2026-07-09):**
   a solo pass (`fan_out`, one model at a time) gives `max_solo`, the ideal-parallel lower bound; a
   concurrent pass (`fan_out_parallel`) gives `parallel_wall`; the tax is
   `(parallel_wall - max_solo) / max_solo`. ~0% ⇒ the GPU timeshares fine and multi-GPU is NOT
   justified; ~+100% ⇒ co-residents serialize and it is. Comparing `sum(solo)` to `max(solo)` — as
   the first harness did — measures arithmetic, not hardware, and cannot falsify either branch.
   **MEASURED 2026-07-10 (Chunk 2 boot): tax = +30%** — controlled for output length (every call
   forced to exactly 256 tokens via `ignore_eos`+`max_tokens`; without that control R1's variable
   chain-of-thought dominates and the number is noise — an uncontrolled run gave a misleading +9%).
   `max_solo` 12.0s → `parallel_wall` 15.7s, dead-stable across 8 queries. **Verdict: multi-GPU is
   NOT justified** — a 4× box buys back at most ~30% latency. So Chunk 4 (below) is dismissed; the
   g6e.12xlarge move stays available only if a *different* need reopens it (32B coder headroom,
   failure isolation, a dedicated judge GPU). Rejected going straight to multi-GPU: ~4× hourly cost
   with no baseline to compare against, and it forces debugging new judge code + new multi-GPU
   surprises (per-GPU device pinning, `CUDA_VISIBLE_DEVICES`, placement) at the same time.

## Cost controls

Budget cap: **$100/mo — confirmed 2026-07-02.** All thresholds below parameterize off it.
(~50h g6e.xlarge on-demand, or ~110h spot.)

| Control | Mechanism | Threshold |
|---|---|---|
| Tiered alerts | AWS Budgets | 50% / 80% / 100% of cap |
| Anomaly detection | AWS Cost Anomaly Detection | default sensitivity |
| Hard stop | SNS → Lambda **stops** tagged instances (not terminate — stopping ends GPU billing, keeps instance recoverable, residual EBS ~$8/mo) | 100% of cap |
| Idle stop | CloudWatch alarm (CPUUtilization < 5% for 30 min) → native EC2 stop action, no Lambda | built in v0.5; GPU/request metric tuned in v2 (native CloudWatch has no GPU metric — needs CW agent/DCGM) |
| Spot | Use where interruption tolerable (most exploratory work) | — |
| Tagging | `project=conclave` on every resource; monthly review | — |

Ordering rule: **budget alerts + hard-stop Lambda exist before the first GPU instance launches.**
Controls first, compute second.

## Phases

- **v0.5 — cost layer, zero GPU spend. ✅ DONE (2026-07-03).** AWS Budgets tiered alerts,
  hard-stop Lambda (SNS-triggered, stops tagged instances), idle-stop CloudWatch alarm (CPU
  proxy, native EC2 stop action), `project=conclave` tagging convention. Both kill switches
  **verified end-to-end** against a throwaway t4g.micro: hard-stop Lambda stopped the tagged
  instance on invoke; idle-stop alarm fired after 30 min sub-5% CPU and stopped it. Total test
  cost ~$0.02. Rationale: manual stop discipline fails exactly once — one forgotten weekend on
  g6e.xlarge ≈ $90 ≈ the whole monthly cap. Hard-stop alone fires only after budget is burned.
- **v1 — single model, working endpoint. ✅ DONE + verified 2026-07-07.** g6e.xlarge on-demand,
  Qwen 2.5 72B AWQ on vLLM, Tailscale-reachable, manual start/stop with idle-stop as safety net.
  Verified: curl from local Mac → Tailscale → vLLM → tokens ("conclave v1 online" verbatim; 17×23
  → 391). Instance torn down after verification (`enable_gpu=false`) to stop spend; EFS keeps the
  weights + the whole stack is `-var enable_gpu=true` away from relaunch. ~1 h on-demand runtime.

  **How it actually went (the useful part):**
  - **Capacity, not quota, was the wall.** Spot G quota approved 2026-07-06 but g6e.xlarge *spot*
    was capacity-starved region-wide (placement scores 1–3/10 every us-east-1 AZ) — repeated
    `InsufficientInstanceCapacity`. Quota = permission ceiling; capacity = physical GPUs free.
    Separate things. On-demand has priority over spot for scarce capacity. Recurred 2026-07-09:
    g6e.xlarge on-demand dry in *all* of us-east-1a/b/c/d (AWS's "try 1b/1c/1d" error text is
    generic boilerplate, not a live capacity read — all three were dry). The G+VT quota is 8 vCPU
    for on-demand **and** spot, so `g6e.2xlarge` (8 vCPU, same single L40S 48 GB, `mem_util`
    values unchanged) is the only fallback size; `g6e.4xlarge` would fail `VcpuLimitExceeded`.
  - **On-demand quota granted overnight** → switched to on-demand (`use_spot=false`). Even then,
    a single pinned AZ (1b) hung ~20 min (provider retries `InsufficientInstanceCapacity` for the
    whole create timeout). We *thought* the fix was `timeouts { create = "3m" }` (fail fast) + an
    **AZ sweep** (1a→1c→1d→1f). 1a dry, **1c had capacity** → launched.
    **That fix does not work — verified 2026-07-09, it hung 26 min with `create = "3m"` set.**
    EC2 returns capacity errors as HTTP 500; the AWS SDK retryer treats 500 as retryable and the
    resource timeout does not govern that retry loop. The error is invisible in normal terraform
    output. A dry AZ therefore *stalls* rather than failing, which makes a bare AZ sweep useless.
    Real recipe (`docs/HANDOFF.md`): `TF_LOG=DEBUG` + `grep InsufficientInstanceCapacity` + an
    external wall-clock guard → ~20s per dry AZ. Orphan-check after every kill: a cancelled
    `RunInstances` returns `context canceled`, so AWS may have launched a box you never saw.
  - **vLLM crash-looped on first serve — KV starvation, not OOM-on-capture.** 72B AWQ (~41.6 GiB
    weights) at `0.92` util + `16384` ctx left only 0.1 GiB for KV (needs 5.0) → `_check_enough_
    kv_cache_memory` ValueError → docker `--restart` → reload 38 GiB from EFS (~6 min) → repeat.
    Fix: `--enforce-eager` (frees CUDA-graph memory for KV, skips ~2 min capture) + `0.95` util +
    `8192` ctx. Persisted in user-data. This card is near its ceiling for 72B; raise ctx only if
    KV budget allows.
  - **EFS cold-load is slow:** 38.74 GiB over NFS4 at ~35 s/shard (model > 27 GiB RAM, so no
    prefetch) ≈ 6 min load every cold start. Acceptable for v1; revisit if cold starts hurt.
  - **Gotchas:** SSM shell doc is `AWS-RunShellScript` (not `-Command`); user-data output goes to
    `/var/log/conclave-init.log` (not console — read via SSM); HF empty-safe wiring worked
    (unauthenticated ungated Qwen pull, no token).
  - Tailscale: Mac on tailnet; GPU joined as `conclave-gpu`; reusable+ephemeral authkey in SSM
    `/conclave/tailscale-authkey`. Untagged for v1.
- **v2 — gateway + fleet. 🔨 in progress.** LiteLLM in front; 3 specialists co-resident on one
  L40S; tune idle-stop metric against real inference activity (LiteLLM request counts); per-model
  cost accounting via LiteLLM.

  **Topology — RESOLVED 2026-07-07: single L40S, 3 small co-resident specialists.** Chose #1 of
  three options (single-GPU co-resident / multi-GPU g6e.12xlarge / multi-instance). Rationale:
  the gateway + judge + ensemble thesis is *fully* exercised on one GPU — LiteLLM routing, per-
  model cost accounting, and the judge (which just calls endpoints) all transfer unchanged to
  multi-GPU later. Multi is scale/cost, layered on in v3 without rework. **The one thing single-
  GPU does NOT teach: realistic parallel-ensemble latency** — co-resident models contend for the
  same SMs, so fan-out partly serializes on compute. Latency realism is deferred to v3 (one
  multi-instance run to measure honestly). Other single-GPU trade-offs accepted for v2: quality
  ceiling (~32B members, no 70B), KV/context squeeze, no failure isolation.

  **Model set — RESOLVED 2026-07-07 (lineage-decorrelated, roles). ⚠️ revised at first boot ↓
  (coder → 14B; reasoning member still open).**
  - Code — **Qwen2.5-Coder-32B AWQ** (~19 GB) · Qwen lineage
  - Reasoning — **DeepSeek-R1-Distill-Llama-8B AWQ** (~5 GB) · Llama lineage
  - General — **Gemma-2-9B AWQ** (~6 GB) · Google lineage
  Why these: the judge feeds on *decorrelated* failure modes, so lineage spread matters more than
  raw scores. First-draft set was secretly Qwen-heavy — R1-Distill-**Qwen** + Qwen-Coder = 2/3
  Qwen. Fixed by moving the reasoning member to the **Llama**-backbone R1 distill (DeepSeek
  distilled R1 into both Qwen and Llama backbones) and the general member to **Gemma** (Google).
  Result: Qwen / Llama / Google — three distinct lineages, exactly one Qwen, spent in the code
  slot where Qwen2.5-Coder-32B is the best open model that fits. Rejected zero-Qwen (DeepSeek-
  Coder-V2-16B for code) — it decorrelates fully but is a weaker coder; keeping Qwen in its
  strongest role is the better trade. Gemma caveat: non-OSI license (Google Gemma Terms, fine for
  private lab) and may be HF-gated (token wired). **2026 landscape note:** the current top open
  coders (GLM-5.2, DeepSeek-V4, Kimi K2.7, Qwen3.6) are all ~1T MoE — none fit one 48 GB card, so
  co-resident v2 lives in 7–32B dense.

  **Build challenge:** partition one 48 GB GPU across 3 vLLM processes — each gets a
  `gpu-memory-utilization` fraction summing under ~0.9, KV cache carved from each slice. This is
  the new technical meat of v2 (and where the co-resident-latency caveat physically lives).

  **First boot 2026-07-07 — architecture PROVEN, reasoning member BLOCKED.** Launched on-demand
  us-east-1c. What works, verified end-to-end (curl → LiteLLM :4000 over Tailscale → routed
  backend → tokens):
  - **LiteLLM gateway + routing** by model name to 3 co-resident vLLM containers on one L40S. ✅
  - **coder** (clean) and **general** (clean) both serve correct output.
  What we learned the hard way (all fixed except the last):
  - **32B coder doesn't fit 3-way.** 32B-Coder + Gemma alone filled 42/44 GiB — the 3rd model
    OOM'd. Dropped coder to **Qwen2.5-Coder-14B AWQ**; all three then fit (per-process CUDA
    context overhead is real, ~2 GB × 3). This is the documented quality-ceiling trade made real;
    the 32B returns in v3 on its own GPU.
  - **RedHatAI w4a16** reasoning quant → vLLM rejects its sparsity config. **jakiAJK AWQ** → AWQ
    kernel wants fp16 but weights are bf16 (`--dtype float16` fixes load).
  - **Reasoning member unresolved — vLLM-0.24 detokenizer bug.** R1-Distill-Llama-8B leaks BPE
    byte markers (`Ġ`=space, `Ċ`=newline) into output. Reproduced across **two independent
    quants** (jakiAJK AWQ + NeuralMagic w8a8) and a tokenizer override — so it's the Llama-distill
    family × vLLM, not the quant. coder (Qwen) + general (Gemma) decode clean, confirming it's
    model-specific. **Open decision (see Open Questions):** switch reasoning to R1-Distill-**Qwen**
    -7B (works, but reintroduces a 2nd Qwen — dents the decorrelation), try a newer vLLM image, or
    a different reasoning model. Box torn down after this to stop spend (~2 h ≈ $4); EFS keeps all
    downloaded weights; relaunch = `enable_gpu=true use_spot=false gpu_az=us-east-1c`.
  - **Offline prep since (2026-07-07, $0, pending launch verification):**
    - Reasoning → **R1-Distill-Qwen-7B FP8** (see Open Q #4 — the "newer vLLM" fix was a dead
      end: 0.24 is already newest).
    - **Per-model cost accounting** wired: `cost_in`/`cost_out` per model → LiteLLM
      `input/output_cost_per_token` + `success_callback: [logging]` (GPU-amortization
      placeholders; calibrate against measured throughput).
    - **Idle-stop on GPU util** (real inference signal): user-data publishes `Conclave/GPUUtil`
      each minute; primary alarm watches it (notBreaching), CPU alarm kept as backstop. IAM gains
      scoped `cloudwatch:PutMetricData`.
    - Next launch verifies all three in one boot.
- **v3 — ensemble + judge. ✅ BUILT AND MEASURED 2026-07-12.** Parallel fan-out, judge
  selection/synthesis, judge evals separate from specialist evals — all built and working.
  Chunk 1 (additive-util KV fix, pinned image, dev_mode idle-stop) + Chunk 2 (contention baseline
  +30%, multi-GPU dismissed) done 2026-07-10; Chunk 3 (judge eval) 2026-07-11, then a rigor pass
  + three adversarial reviews 2026-07-12.
  **Result: the fleet fails the pattern's PRECONDITION.** `HEADROOM = ORACLE − BEST SINGLE MODEL`
  = **+0.028** (CI [+0.003, +0.052]) — even a *perfect* judge barely beats always calling the
  strongest model alone (0.961 vs 0.933), and the real judge lands below it (0.883). Cause: the
  candidates are **redundant** — 28/36 queries are an exact TIE; strict wins are coder 4 /
  general 4 / reasoning 0. ⚠️ But the measurement is **ceiling-limited** (31/36 queries at the
  grader's max, so the result rests on 5 queries) and the verdict is **NOT RESOLVED at n=36**.
  **Next: HARDER QUERIES that de-saturate the grader, then re-run `orchestrator/divergence.py`
  ($0) — only then can the fleet be judged.** An earlier "the 14B coder wins 31/36" claim was a
  config-ordering artifact and is **retracted**. Also revisit the router ("cheap sibling" above).
  An earlier "thesis proven / the small judge holds up" claim was **withdrawn** — see
  `docs/chunk3-judge-eval-results.md` for the full correction and the three over-claims it retracts.
  Chunk 4 dismissed.
- **v4 (maybe) — MCP front-end.** Unpauses project #5: MCP server as the structured interface
  to the platform.

## Open questions

1. **v1 model choice — RESOLVED 2026-07-06: Qwen 2.5 72B AWQ (`Qwen/Qwen2.5-72B-Instruct-AWQ`).**
   First picked Llama 3.3 70B AWQ on "fewest first-boot surprises," then the infra reality
   inverted that: user-data passes no `HF_TOKEN`, so only *ungated* HF repos download. Llama
   3.3 70B is HF-gated (token + Meta license); Qwen 2.5 72B AWQ is ungated, Apache 2.0, and was
   already the wired default. So on *this* infra Qwen is the safer first boot, and it's equally
   70B-class dense — proves the same single-GPU serving thesis. Swap to Llama in v1.1/v2 once an
   HF token is wired into SSM + user-data (gated models need it eventually anyway).
   Weights-landscape check at decision time:
   - Design's original binary still holds for *70B-class dense* — Qwen3 put its gains into MoE
     (235B-A22B, too big) and smaller dense (tops out at **32B**); there is no Qwen3 72B. Llama 4
     is also MoE (needs MoE-aware serving, awkward on one 48 GB). So the 72B-dense slot is still
     Qwen 2.5 72B, and 70B-dense is still Llama 3.3 70B.
   - **Finding to exploit later (v2/v3):** best model-per-GPU may not be 70B-class at all.
     Qwen3 32B / Qwen3.6-27B dense reportedly match last-gen 70B in thinking mode, fit at FP8
     (~14 GB) / FP16 (~28 GB) with large KV headroom, and run faster. When the platform stands
     up for real, pick by best-fit (capability ÷ VRAM ÷ latency), not by param count. Revisit the
     fleet composition in v2 with this in mind.
2. **Spot vs on-demand for v1 — RESOLVED 2026-07-06: spot.** Spot G/VT quota approved (8 vCPU,
   CASE_CLOSED); On-Demand G/VT still pending (0 vCPU, CASE_OPENED 2026-07-03). Original doc
   preferred on-demand ("interruption annoys"), but v1's done-criteria is trivial + restartable
   (curl returns tokens) and EFS holds the weights, so a mid-session reclaim loses nothing. Switch
   v1 (and the default) to on-demand once its quota approves; spot remains the v2+ default anyway.
3. **Ensemble-phase instance — SEQUENCING RESOLVED 2026-07-08 (see v3 locked decisions).** v3
   starts on the existing single g6e.xlarge to build the orchestrator/judge/API/eval + capture the
   co-resident SM-contention baseline; multi-GPU is a measured follow-on. *Still open:* the
   multi-GPU box itself — g5.12xlarge vs g6e.12xlarge (4× L40S) — price/perf-compare when the
   single-GPU baseline is in hand.
4. **v2 reasoning member — RESOLVED 2026-07-07: R1-Distill-Qwen-7B (FP8) for v2.** The Llama
   distill leaks BPE byte markers (`Ġ`/`Ċ`) under vLLM 0.24 — reproduced across jakiAJK AWQ +
   NeuralMagic w8a8, so it's the Llama-distill × vLLM, not the quant (coder/general decode clean).
   The preferred fix (bump vLLM) was a **dead end — 0.24 is already the newest release**. So v2 uses
   `RedHatAI/DeepSeek-R1-Distill-Qwen-7B-FP8-dynamic` (Qwen tokenizer decodes clean, FP8 native on
   the L40S, ~7.5 GB). Trade accepted: a 2nd Qwen member dents lineage decorrelation for v2.
   **The V0-engine restoration hypothesis is FALSIFIED (2026-07-10, Chunk 2):** jakiAJK Llama-8B AWQ
   under `VLLM_USE_V1=0` STILL leaks `Ġ`/`Ċ`, so the bug is NOT V1-detokenizer-specific. Restoring a
   Llama-lineage reasoner now waits on a future vLLM release or a different model/quant — not an
   engine flag. (Only the AWQ quant was V0-tested; w8a8-under-V0 untested but same expectation.)

## Launch-day risks (v1 first boot)

Known failure modes, ranked by likelihood. Debug access is Session Manager
(`aws ssm start-session`), no SSH — read `docker logs vllm` and
`/var/log/conclave-init.log`.

1. **DLAMI driver ↔ vLLM CUDA mismatch** — most common. Container CUDA newer than
   host driver → vLLM won't start. Fix: pin the vLLM image tag to the DLAMI's driver.
2. **AWQ ↔ L40S kernel support** — some AWQ configs need a specific vLLM version or
   `--quantization awq_marlin`.
3. **KV-cache OOM at `--max-model-len 16384`** — 72B AWQ + KV cache on 48 GB may OOM.
   Drop to 8192 if so.
4. **Idle-stop false-negative** — vLLM idle CPU may sit above 5%, so the CPU-proxy
   alarm never fires. Watch the first real GPU session; bump threshold or move to a
   GPU/request metric earlier than v2 if needed.
5. **Tailscale key expiry** — key is non-ephemeral, but disable expiry on the
   `conclave-gpu` node in the Tailscale console after first join, or a later reboot
   can't rejoin.

Model for v1: **Qwen2.5-72B-Instruct-AWQ** (ungated — no HF token friction on first
boot). Launch on-demand, not spot, for the first session.
