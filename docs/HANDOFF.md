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
- **v3 (ensemble + judge):** 🔨 **in progress.** Design locked; orchestrator built +
  live-smoke-verified 2026-07-08; Chunk 1 infra fixes merged. Next = Chunk 2 baseline boot (below).

## The ONE next action

**Chunk 2 — contention baseline (a paid boot, ~45min, ~$2-3).** One boot does double duty: it
**validates Chunk 1** (does the fleet boot clean, zero restarts, no dev_mode reap?) and **captures
the baseline** (real seq-vs-ideal-parallel latency — the number that justifies multi-GPU).
Steps: boot with `-var dev_mode=true` (playbook below), set the gateway, run
`CONCLAVE_GW=<ts-ip>:4000 python3 orchestrator/harness.py --baseline`, then tear down. If a model
still KV-starves, live-tune its ceiling in `variables.tf` (utils are an estimate) and persist.

**v3 status.** Four design decisions locked (see `design.md` § "v3 locked decisions"): client-side
orchestrator · pluggable judge, default in-fleet Gemma · OpenAI-compatible `model=ensemble` + debug
metadata · single-GPU baseline first, multi-GPU follow-on. Orchestrator (`orchestrator/ensemble.py`)
built + **live-smoke-verified** 2026-07-08; live harness in `orchestrator/harness.py`
(`--baseline` for the contention run, default = smoke; gateway via argv or `$CONCLAVE_GW`).

### The v3 chunk plan (breakdown + time scope)
- **Chunk 1 — pre-boot infra fixes. ✅ DONE + merged (PR #4, `c8919c7`).** `dev_mode` idle-stop +
  cumulative-util/sequential-start KV fix. Both unverified until Chunk 2's boot.
- **Chunk 2 — contention baseline. ← NEXT.** Boot · ~45min · ~$2-3. Validates Chunk 1 + captures
  the baseline. Rough prior datapoint (from the reaped run's one clean smoke): co-residency ≈ +40%
  over ideal-parallel — replace with a real measurement.
- **Chunk 3 — judge eval (the thesis payload).** ~2h harness build (no boot) + ~45min boot ·
  ~$2-3 + tiny frontier API. Build a labeled query set (~15-20 Qs across coder/reasoning/general
  strengths) + a scorer; run `judge=gemma` vs a frontier `judge_url`/`judge_model`; compare
  selection/synthesis quality. Output: "does a small self-hosted judge hold up vs frontier."
- **Chunk 4 — multi-GPU.** ~3-4h build + ~1h measure · ~4× hourly (~$8-15/measure). Only once
  Chunk 2 justifies it. Pick box (g5.12xlarge vs g6e.12xlarge), per-GPU placement
  (`CUDA_VISIBLE_DEVICES`/device pinning), measure the parallel delta, unlock 32B coder +
  Llama-reasoner restoration.

Total remaining v3 ≈ 2-3 focused sessions. Pad boot chunks 1.5× — every boot has surprised us.

### Idle-stop was dev-hostile — FIXED in Chunk 1 (`dev_mode`), verify in Chunk 2
The GPU idle-stop alarm stops the box after 30 min of GPUUtil < 5%. During interactive dev — boot
debugging, restart-roulette watching, sparse requests — util sits under 5% even while you're
working, so it reaped the box mid-baseline 2026-07-08. **Fix (committed, unverified until a boot):**
`dev_mode` tfvar (`infra/variables.tf` + `gpu.tf`) widens the window to 90 min when true; default
30 min stays for unattended boots. Boot dev with `-var dev_mode=true`. Escape hatch if a session
still runs long: `aws cloudwatch disable-alarm-actions --alarm-names conclave-idle-stop-gpu
conclave-idle-stop-gpu-cpu-backstop --profile yeti-conclave`.

### Co-residency KV race — FIXED in Chunk 1 (cumulative utils + sequential start), verify in Chunk 2
Both v2 boots hit `Available KV cache memory: -12.1 GiB`: vLLM's `mem_util` is a per-process
*ceiling on TOTAL GPU mem*, not a private slice, so racing all 3 at boot means a late starter's
ceiling falls below what the others already hold → negative KV → crash-loop (self-recovered only
via restart-roulette). **Fix (committed, unverified until a boot):** (1) `models` var reordered to
start sequence general→coder→reasoning with CUMULATIVE ceilings 0.25/0.55/0.82 (each ≈ everything
resident once it loads); (2) user-data now starts one model at a time, waiting for each
`Application startup complete` before the next, so each profiles against true residency. **Chunk 2
must confirm this boots clean with zero restarts** — if a model still KV-starves, nudge its (and
later) ceilings up; the values are an estimate. If it regresses, the old restart-roulette (crash
until the race resolves, ~3-5 min) is the fallback behaviour.

## v2 boot playbook (reuse for any GPU boot)

1. Re-auth: `aws sso login --profile yeti-conclave` (token expires between sessions).
2. Launch: from `infra/` — **add `-var dev_mode=true` for any interactive boot** (widens idle-stop
   to 90m so debugging doesn't get reaped; see below) —
   `terraform apply -var enable_gpu=true -var dev_mode=true -var use_spot=false -var gpu_az=us-east-1c`
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
