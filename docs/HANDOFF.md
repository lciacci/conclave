# HANDOFF — resume here

Last updated: 2026-07-07 (end of session). Read this + `design.md` to resume cold.

## Where we are

- **v0.5 (cost layer):** ✅ done + verified.
- **v1 (single 70B):** ✅ done + verified. Qwen 2.5 72B AWQ served tokens over Tailscale, torn down.
- **v2 (gateway + 3-model fleet):** 🔨 **one clean boot from done.** Architecture proven last
  session (LiteLLM gateway → routes to 3 co-resident vLLM on one L40S → tokens; coder + general
  clean). All fixes + offline prep committed. Nothing running, $0 spend, git synced (`fd7e276`).

## The ONE next action

Verify v2 in a single paid boot (~$2-4, ~30-60 min). Steps:

1. Re-auth: `aws sso login --profile yeti-conclave` (token expires between sessions).
2. Launch: from `infra/` —
   `terraform apply -var enable_gpu=true -var use_spot=false -var gpu_az=us-east-1c`
   (on-demand; us-east-1c had capacity last two launches. If `InsufficientInstanceCapacity`,
   sweep AZs: retry with `-var gpu_az=us-east-1a` / `1d` / `1f`. The 3-min create timeout fails
   fast so a sweep is quick.)
3. Babysit the boot via SSM (no SSH). **SSM doc is `AWS-RunShellScript`** (not `-Command`).
   Instance id from `terraform output gpu_instance_id`. Useful:
   - `docker ps -a --format '{{.Names}} :: {{.Status}}'` — expect vllm-coder / vllm-reasoning /
     vllm-general + litellm all `Up`.
   - `docker logs vllm-<name> --tail 30` — user-data output is in `/var/log/conclave-init.log`,
     NOT console.
   - `nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader`
   - Tailscale IP: `tailscale status | grep conclave` (last session was 100.97.137.99; new each boot).
4. **Verify three things** (this is what makes v2 "done"):
   - **All 3 serve CLEAN** through the gateway (tailscale-ip:4000). Test each: `coder`,
     `reasoning`, `general`. Reasoning is the one to watch — confirm NO `Ġ`/`Ċ` byte-marker leak
     (that was the Llama-distill bug; we switched to the Qwen distill to fix it — see below).
   - **Per-model cost accounting** shows in `docker logs litellm` (model + tokens + response_cost).
   - **GPU-util idle-stop metric** flows: `aws cloudwatch get-metric-statistics --namespace
     Conclave --metric-name GPUUtil ...` returns datapoints.
5. **Tear down to stop spend:** `terraform apply -var enable_gpu=false`. EFS keeps weights.

## What's already de-risked (fixes committed, will apply automatically)

- **Model set (in `infra/variables.tf` `models` var):** coder=`Qwen/Qwen2.5-Coder-14B-Instruct-AWQ`,
  reasoning=`RedHatAI/DeepSeek-R1-Distill-Qwen-7B-FP8-dynamic`, general=`hugging-quants/gemma-2-9b-it-AWQ-INT4`.
  Utils 0.28/0.20/0.18 (sum 0.66, fits 48 GB). Each `--enforce-eager`.
- **Why 14B not 32B coder:** a 32B + 2 small can't co-reside (32B+Gemma alone filled 42/44 GiB).
  32B returns in v3 on its own GPU.
- **Why Qwen-7B not Llama-8B reasoning:** the Llama distill leaks BPE byte markers under vLLM
  0.24's V1 detokenizer (reproduced across 2 quants; 0.24 is newest, no image bump). Qwen
  tokenizer decodes clean. Trade: 2nd Qwen dents lineage decorrelation — restoring a Llama
  reasoner is a v3 experiment (try V0 engine `VLLM_USE_V1=0`).
- **Cost accounting + GPU-util idle-stop:** wired in user-data + gpu.tf, unverified until boot.
- **Gemma HF token:** real key already in SSM `/conclave/hf-token` (Gemma is gated).

## Likely landmines (pattern: nearly every boot surprises us)

- **FP8 reasoning untested here** — may need a util bump if KV-starved (v1/v2 both needed KV
  tuning). Symptom: crash-loop with `_check_enough_kv_cache_memory` → raise its `mem_util` or drop
  `max_len`, live-swap via SSM then persist to `variables.tf`.
- **Per-vLLM mem partitioning is empirical** — actual usage ran higher than nominal util last
  time. If a model OOMs on load (`Free memory ... less than desired`), lower utils and restart clean.
- **us-east-1c capacity** — on-demand can be dry; AZ-sweep as above.

## After v2 is verified → v3 (the thesis)

Ensemble fan-out + the **judge** (meta-reasoner selecting/synthesizing across the 3 parallel
responses). Also the natural home for: the 32B coder on its own GPU (multi-instance), realistic
parallel-ensemble latency (deferred from v2 — co-resident models contend for SMs), and restoring
a Llama-lineage reasoner. See `design.md` v3 + open questions.

## Working conventions reminder

Push back on drift; numbered decisions before committing; log suggestion-gates
(`python3 scripts/gate/emit.py --fired --kind <k> --note "..."`); never leave a GPU running without
idle-stop wired; Tailscale-only, no public ports; every AWS resource carries `project=conclave`
(provider default_tags).
