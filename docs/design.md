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
- **v1 — single model, working endpoint. ⏳ blocked: spot capacity.** g6e.xlarge spot, Qwen
  2.5 72B AWQ on vLLM, Tailscale connected, manual start/stop with idle-stop as safety net.
  Done = curl from local Mac through Tailscale returns tokens. Terraform written and gated behind
  `enable_gpu`; the GPU *instance* + its alarm are gated, the surrounding EFS cache, security
  groups, and IAM role are already applied (all $0 while empty).

  **Launch attempt 2026-07-06 — everything green except capacity:**
  - Spot G/VT quota approved (8 vCPU). On-Demand G/VT still pending (0 vCPU) — can't fall back.
  - Tailscale: Mac on tailnet (`100.120.231.43`); real reusable+ephemeral authkey stored in SSM
    `/conclave/tailscale-authkey` (v2, replaced the `PASTE-HERE` placeholder). Untagged for v1.
  - Model: Qwen 2.5 72B AWQ, ungated — no HF token needed (Llama 3.3 70B is HF-gated; deferred).
  - Infra changes made + working: `use_spot` var, spot `instance_market_options` in gpu.tf,
    `gpu_az` var (default `us-east-1b`) + AZ-filtered subnet data source (was pinned to `ids[0]`
    = us-east-1d, which was spot-dry). EFS has a mount target in every AZ, so any AZ is fine.
  - **Blocker:** `InsufficientInstanceCapacity` for g6e.xlarge spot. First hit in us-east-1d;
    after AZ-hop to 1b, still failed. Spot placement scores 1–3/10 across all us-east-1 AZs =
    region-wide scarcity, not a config bug. Both applies killed cleanly, zero spend, state clean.
  - **To resume:** re-check `aws ec2 get-spot-placement-scores --instance-types g6e.xlarge
    --target-capacity 1 --region-names us-east-1 --single-availability-zone`; when an AZ scores
    high, set `-var gpu_az=<that AZ>` and `terraform apply -var enable_gpu=true` (profile
    `yeti-conclave`). OR launch on-demand once its quota lands (`-var use_spot=false`). Off-peak
    hours tend to have better spot capacity. Uncommitted working-tree changes hold all the above.
- **v2 — gateway + fleet.** LiteLLM in front; add 2–3 specialized models (code, reasoning,
  small/fast); tune idle-stop metric against real inference activity (LiteLLM request counts);
  per-model cost accounting via LiteLLM.
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
