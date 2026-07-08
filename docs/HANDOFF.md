# HANDOFF — resume here

Last updated: 2026-07-08 (end of session). Read this + `design.md` to resume cold.

## Where we are

- **v0.5 (cost layer):** ✅ done + verified.
- **v1 (single 70B):** ✅ done + verified. Qwen 2.5 72B AWQ served tokens over Tailscale, torn down.
- **v2 (gateway + 3-model fleet):** ✅ **done + verified end-to-end** (2026-07-08 boot on
  g6e.xlarge on-demand, us-east-1c). All 3 (coder/reasoning/general) served CLEAN through the
  LiteLLM gateway — reasoning had NO byte-marker leak (Qwen distill fix holds). Per-model cost
  accounting prints (`response_cost` matched configured $/token exactly). GPUUtil idle-stop metric
  flowed. Torn down, $0 spend. One landmine hit + fixed (see below), fixes committed.

## The ONE next action

**v3 in progress.** Four design decisions locked 2026-07-08 (see `design.md` § "v3 locked
decisions"): client-side orchestrator · pluggable judge, default in-fleet Gemma · OpenAI-compatible
`model=ensemble` + debug metadata · single-GPU contention baseline first, multi-GPU follow-on.
Orchestrator scaffolded + verified offline: `python3 orchestrator/ensemble.py` (canned call fn, no
GPU). It exposes `ensemble(query, cfg, call)` — fan-out → judge → OpenAI-shaped result with
per-model latency + judge rationale in `metadata`.

Next (in order):
1. **Live smoke test — ✅ DONE 2026-07-08.** Ran a real ensemble through the gateway from the local
   client. Fan-out served all 3 (coder 1.8s / reasoning 11.4s / general 2.9s); Gemma judge selected
   + rationalized; OpenAI-shaped result returned. Two bugs found + fixed live (commit `76db079`):
   Gemma-2 has NO system role (400s) → judge instruction folded into the user turn; and `run_judge`
   now degrades to a candidate instead of crashing when the judge call errors.
2. **Contention baseline — REAPED, redo next boot.** The idle-stop alarm stopped the box mid-run
   (see landmine below), so the numbers are garbage (uniform 75 s = requests hitting a dying box).
   One clean smoke datapoint stands: sequential fan-out wall ≈ Σ per-model ≈ 16 s vs slowest-model
   ≈ 11 s, i.e. co-residency adds ~40% over ideal-parallel — but redo properly with the alarm
   suspended. Script: `scratchpad/baseline.py` (points at the gateway, prints seq/ideal/tax).
3. **Judge eval** — same ensemble with `judge=gemma` vs a frontier `judge_url`/`judge_model`;
   compare selection quality. Then decide multi-GPU box (g5.12xlarge vs g6e.12xlarge).

### ⚠️ Idle-stop is dev-hostile — SUSPEND IT during interactive work
The GPU idle-stop alarm stops the box after **30 min of GPUUtil < 5%** (`infra/gpu.tf`,
`idle_minutes=30`). During interactive dev — boot debugging, restart-roulette watching, sparse
test requests — the 5-min-averaged util sits under 5% even though you're actively working, so the
alarm reaps the box mid-session (it did exactly this 2026-07-08, killing the baseline). The alarm
is doing its job (reaping forgotten boxes); it's just blind to interactive use. **Right after boot,
suspend the actions:**
`aws cloudwatch disable-alarm-actions --alarm-names conclave-idle-stop-gpu conclave-idle-stop-gpu-cpu-backstop --profile yeti-conclave`
and re-enable (`enable-alarm-actions`) or just tear down when done. Keeps the safety net's intent
without the reap-during-dev trap. (Candidate v3 fix: a `dev_mode` var that widens `idle_minutes` or
skips the alarm, vs. a heartbeat sentinel metric — decide next chunk.)

### Co-residency restart-roulette still happens at util 0.86
Even with the committed 0.86 utils, coder + reasoning still lose the KV race on a cold boot
(`Available KV cache memory: -12.1 GiB` — vLLM's util is a per-process *ceiling* on TOTAL GPU mem,
so a late starter sees the others' weights against its budget). They self-recover after 1-3 docker
restarts (general wins first, they retry into the freed window) — both boots converged this way in
~3-5 min. Deterministic fix deferred to a v3 chunk: **stagger the container starts in user-data**
(start general → wait for `Application startup complete` → coder → reasoning) instead of racing all
three at once. Until then: poll `docker logs vllm-<m> | grep "Application startup complete"` and be
patient; err-count in logs is cumulative across restarts, not a permanent-failure signal.

## v2 boot playbook (reuse for any GPU boot)

1. Re-auth: `aws sso login --profile yeti-conclave` (token expires between sessions).
2. Launch: from `infra/` —
   `terraform apply -var enable_gpu=true -var use_spot=false -var gpu_az=us-east-1c`
   (on-demand; us-east-1c had capacity 3 launches running. If `InsufficientInstanceCapacity`,
   sweep AZs: `-var gpu_az=us-east-1a` / `1d` / `1f`.) Instance create took ~10 min last boot.
3. Babysit the boot via SSM (no SSH). **SSM doc is `AWS-RunShellScript`** (not `-Command`).
   **sh, not bash** — no `declare -A`/assoc arrays; for multiline scripts, base64-encode locally
   and send `echo <b64> | base64 -d | bash` (the CLI `commands=[...]` shorthand mangles newlines).
   Instance id from `terraform output gpu_instance_id`. Useful:
   - `docker ps -a --format '{{.Names}} :: {{.Status}}'` — expect vllm-coder / vllm-reasoning /
     vllm-general + litellm all `Up`. But `Up` ≠ ready: grep logs for `Application startup complete`.
   - `docker logs vllm-<name> --tail 30` — user-data output is in `/var/log/conclave-init.log`.
   - `nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader`
   - Tailscale IP: `tailscale status | grep conclave` (2026-07-08 was 100.123.46.105; new each boot).
     The local machine is on the tailnet — test the gateway directly at `<ts-ip>:4000`.
4. **Verify three things** (what makes v2 "done"):
   - **All 3 serve CLEAN** through the gateway. Reasoning is the one to watch — NO `Ġ`/`Ċ` leak.
   - **Per-model cost accounting** in `docker logs litellm | grep response_cost` (needs
     `LITELLM_LOG=DEBUG`, now baked into user-data — see below).
   - **GPUUtil metric** flows: `aws cloudwatch get-metric-statistics --namespace Conclave
     --metric-name GPUUtil ...` (0.0 between requests is correct — idle).
5. **Tear down to stop spend:** `terraform apply -var enable_gpu=false`. EFS keeps weights.
   (Destroy took ~6 min last time.)

## What's already de-risked (fixes committed, will apply automatically)

- **Model set (in `infra/variables.tf` `models` var):** coder=`Qwen/Qwen2.5-Coder-14B-Instruct-AWQ`,
  reasoning=`RedHatAI/DeepSeek-R1-Distill-Qwen-7B-FP8-dynamic`, general=`hugging-quants/gemma-2-9b-it-AWQ-INT4`.
  **Utils now 0.36/0.26/0.24 (sum 0.86).** The old 0.28/0.20/0.18 (sum 0.66) crashed all 3 on load
  with `_check_enough_kv_cache_memory` — each slice ≈ its own weights, ~0 for KV, while 34% of the
  GPU sat idle. Co-resident vLLM counts util as a fraction of TOTAL mem, so late starters see others'
  weights against their budget (coder lost the KV race twice, then won). 0.86 boots all 3 clean.
  Each `--enforce-eager`.
- **Why 14B not 32B coder:** a 32B + 2 small can't co-reside (32B+Gemma alone filled 42/44 GiB).
  32B returns in v3 on its own GPU.
- **Why Qwen-7B not Llama-8B reasoning:** the Llama distill leaks BPE byte markers under vLLM
  0.24's V1 detokenizer (reproduced across 2 quants; 0.24 is newest, no image bump). Qwen
  tokenizer decodes clean. Trade: 2nd Qwen dents lineage decorrelation — restoring a Llama
  reasoner is a v3 experiment (try V0 engine `VLLM_USE_V1=0`).
- **Cost accounting + GPU-util idle-stop:** ✅ verified 2026-07-08. Cost needs `LITELLM_LOG=DEBUG`
  (the `success_callback: ["logging"]` alone never prints `response_cost`) — now baked into user-data.
- **Gemma HF token:** real key already in SSM `/conclave/hf-token` (Gemma is gated).

## Likely landmines (pattern: nearly every boot surprises us)

- **FP8 reasoning** — booted clean at util 0.26 (2026-07-08), R1 chain-of-thought output is valid
  UTF-8. Was the theorized KV risk; the util bump covered it. Symptom if it recurs on a smaller
  util: crash-loop with `_check_enough_kv_cache_memory` → raise `mem_util` or drop `max_len`,
  live-swap via SSM then persist to `variables.tf`.
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
