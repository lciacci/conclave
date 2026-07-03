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
- **v1 — single model, working endpoint. ⏳ quota-gated.** g6e.xlarge, one 70B AWQ model on
  vLLM, Tailscale connected, manual start/stop with idle-stop as safety net. Done = curl from
  local Mac through Tailscale returns tokens. Terraform written and gated behind `enable_gpu`;
  the GPU *instance* + its alarm are gated, but the surrounding EFS cache, security groups, and
  IAM role are already applied (all $0 while empty). Blocked on G-instance vCPU quota approval
  (requested 2026-07-03, both On-Demand + Spot PENDING).
- **v2 — gateway + fleet.** LiteLLM in front; add 2–3 specialized models (code, reasoning,
  small/fast); tune idle-stop metric against real inference activity (LiteLLM request counts);
  per-model cost accounting via LiteLLM.
- **v3 — ensemble + judge.** Parallel fan-out, judge selection/synthesis, judge evals separate
  from specialist evals. The pedagogically interesting phase.
- **v4 (maybe) — MCP front-end.** Unpauses project #5: MCP server as the structured interface
  to the platform.

## Open questions

1. **v1 model choice** — Llama 3.3 70B AWQ vs Qwen 2.5 72B AWQ. Decide at launch; check
   current weights landscape then, not now.
2. **Spot vs on-demand for v1** — spot saves ~50–70% but interruption mid-session annoys.
   Suggest on-demand for v1 (short manual sessions), spot from v2 automation onward.
3. **Ensemble-phase instance** — g5.12xlarge vs g6e.12xlarge. Price-compare when v3 starts.
