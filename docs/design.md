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
    Separate things. On-demand has priority over spot for scarce capacity.
  - **On-demand quota granted overnight** → switched to on-demand (`use_spot=false`). Even then,
    a single pinned AZ (1b) hung ~20 min (provider retries `InsufficientInstanceCapacity` for the
    whole create timeout). Fix: `timeouts { create = "3m" }` (fail fast) + an **AZ sweep**
    (1a→1c→1d→1f). 1a dry, **1c had capacity** → launched.
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
- **v3 — ensemble + judge.** Parallel fan-out, judge selection/synthesis, judge evals separate
  from specialist evals. The pedagogically interesting phase.
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
3. **Ensemble-phase instance** — g5.12xlarge vs g6e.12xlarge. Price-compare when v3 starts.
4. **v2 reasoning member — RESOLVED 2026-07-07: R1-Distill-Qwen-7B (FP8) for v2.** The Llama
   distill leaks BPE byte markers (`Ġ`/`Ċ`) under vLLM 0.24's V1 detokenizer — reproduced across
   jakiAJK AWQ + NeuralMagic w8a8, so it's the Llama-distill × vLLM, not the quant (coder/general
   decode clean). The preferred fix (bump vLLM) was a **dead end — 0.24 is already the newest
   release**. So v2 uses `RedHatAI/DeepSeek-R1-Distill-Qwen-7B-FP8-dynamic` (Qwen tokenizer decodes
   clean, FP8 native on the L40S, ~8 GB). Trade accepted: a 2nd Qwen member dents lineage
   decorrelation for v2. **v3 experiment to restore a Llama-lineage reasoner:** try the V0 engine
   (`VLLM_USE_V1=0`) — the bug is V1-detokenizer-specific — or a future vLLM release.

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
