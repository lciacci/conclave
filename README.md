# Conclave

A self-hosted multi-model inference platform on AWS. Open-weight models
(70B-class) served behind an OpenAI-compatible gateway, reachable only over a
private WireGuard mesh, with cost controls wired in before the first GPU ever
boots.

The interesting part is not running one model — it's the **judge**: fanning a
query out to several specialized models in parallel and using a meta-reasoner
to select or synthesize the best answer.

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
                                              vLLM ──▶ 70B AWQ model
                                               │
                                          EFS model cache
                                    (S3 cold archive for inactive weights)
```

- **Compute:** g6e.xlarge — one L40S (48 GB, 864 GB/s). A 70B model at 4-bit
  fits a single GPU; single-GPU serving beats 4× A10G tensor-parallel for one
  model (no inter-GPU sync). Scales to 4× L40S only when multiple models must
  be resident at once.
- **Serving:** vLLM (continuous batching, paged attention) — chosen over Ollama
  precisely because it exposes the serving internals worth learning.
- **Gateway:** LiteLLM — one OpenAI-compatible API in front of every backend,
  and the natural seam for per-model cost accounting.
- **Network:** Tailscale mesh, zero public ingress. Session Manager for shell
  access — no SSH keys, no port 22.
- **Storage:** EFS for the active model cache (instance-type-agnostic, survives
  teardown), S3 for cold weights, EBS for the OS volume only.

Full reasoning for every locked decision lives in [`docs/design.md`](docs/design.md).

## Cost controls (built first, on purpose)

Controls exist **before** the first GPU instance launches. A forgotten weekend
on a running GPU burns roughly the entire monthly cap.

| Control | Mechanism |
|---|---|
| $100/mo budget, tiered alerts | AWS Budgets (50 / 80 / 100%) |
| Hard stop | 100% breach → SNS → Lambda stops every tagged instance |
| Idle stop | CloudWatch alarm (CPU < 5% / 30 min) → native EC2 stop action |
| Anomaly detection | AWS Cost Anomaly Detection, daily email |
| Spot + tagging | Spot where interruption is tolerable; every resource tagged |

The hard-stop kill switch and idle-stop were both verified end-to-end against a
throwaway micro instance before any GPU spend.

## The judge (the thesis)

The arbiter, judge, mediator, critic, and router are one family:
**meta-reasoners over specialized outputs.** They differ in ground truth,
latency budget, and what they optimize. Conclave's judge does *selection /
synthesis* over parallel responses to the same query, on every request — a
distinct problem from a triage arbiter that runs once. Naming the family is
what turns "two similar components" into an exploration of a design pattern.

## Phases

| Phase | State | Scope |
|---|---|---|
| **v0.5** | ✅ done | Cost layer — budget, hard-stop, idle-stop, tagging. Zero GPU spend. |
| **v1** | ⏳ capacity-blocked | Qwen 2.5 72B AWQ on vLLM, Tailscale-reachable endpoint. All preconditions green; g6e.xlarge spot capacity-starved in us-east-1 (2026-07-06). Retry when capacity returns or on-demand quota lands. |
| **v2** | planned | LiteLLM gateway, 2–3 specialized models, per-model cost accounting. |
| **v3** | planned | Ensemble fan-out + judge selection/synthesis. |
| **v4** | maybe | MCP server as the structured front-end to the platform. |

## Stack

Terraform · AWS (EC2 G-instances, EFS, S3, Lambda, Budgets, CloudWatch) ·
vLLM · LiteLLM · Tailscale · Python

## Layout

```
docs/design.md      decision log — every locked call with its reasoning
infra/              Terraform: cost layer (applied) + v1 GPU module (gated)
  budget.tf         budget + tiered alerts
  hardstop.tf       SNS → Lambda kill switch
  idle_stop.tf      idle CloudWatch alarm
  gpu.tf / efs.tf   v1 instance + model cache (enable_gpu gated)
  user-data.sh.tftpl  first-boot: tailscale join, EFS mount, vLLM serve
```

---

Built with [Tessera](https://github.com/lciacci) — a decision-tracking
development framework. Design decisions here are logged as they were made, not
reconstructed after.
