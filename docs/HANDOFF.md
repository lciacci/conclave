# HANDOFF — resume here

Last updated: 2026-07-10 (end of session). Read this + `design.md` to resume cold.

## Where we are

- **v0.5 (cost layer):** ✅ done + verified.
- **v1 (single 70B):** ✅ done + verified. Qwen 2.5 72B AWQ served tokens over Tailscale, torn down.
- **v2 (gateway + 3-model fleet):** ✅ **done + verified end-to-end** (2026-07-08 boot on
  g6e.xlarge on-demand, us-east-1c). All 3 (coder/reasoning/general) served CLEAN through the
  LiteLLM gateway — reasoning had NO byte-marker leak (Qwen distill fix holds). Per-model cost
  accounting prints (`response_cost` matched configured $/token exactly). GPUUtil idle-stop metric
  flowed. Torn down, $0 spend. One landmine hit + fixed (see below), fixes committed.
- **v3 (ensemble + judge):** 🔨 **in progress.** Design locked; orchestrator built +
  live-smoke-verified. Chunk 1 fixed + Chunk 2 done (2026-07-10 boot, below). Chunk 4 dismissed.
  Next = **Chunk 3, the judge eval** — the thesis payload, no prior work started.

## The ONE next action

**Chunk 3 — judge eval (the thesis payload). HARNESS BUILT 2026-07-11 (offline, no boot); remaining =
one ~45min boot · ~$2-3 + a tiny frontier API bill.** "Does a small self-hosted judge hold up vs a
frontier judge?" Built + offline-verified this session (all `--demo` self-checks pass):
- `orchestrator/eval_queryset.py` — 18 labeled queries (6 coder / 6 reasoning / 6 general), each with
  a gold reference. Divergent-by-design so the judge's choice matters.
- `orchestrator/candidate_cache.py` — one boot fans every query to the fleet, caches candidates to
  `eval_candidates.json` (gitignored); eval iterates offline forever after. All-error guard so a
  gateway blip isn't frozen in.
- `orchestrator/judge_eval.py` — pluggable Scorer protocol (LocalHeuristic = offline/CI backstop;
  ReferenceGrader = LLM grades 0-5 vs the reference, the real number). Provider-agnostic judge/grader
  via `ensemble.http_call` keyed with `functools.partial` (added an `api_key` param — one line).
  Three phases decoupled by JSON: `--generate` (boot: candidates + Gemma judgments), `--frontier`
  (offline: frontier judge over the cache), `--score [--heuristic]` (offline: score + head-to-head).

**Run order to finish Chunk 3:**
1. Boot the fleet (v2 playbook + `scripts/sweep-gpu-capacity.sh g6e.xlarge us-east-1c ...`, `dev_mode=true`).
2. `CONCLAVE_GW=<ts-ip>:4000 python3 orchestrator/judge_eval.py --generate` → candidates + Gemma judgments cached. **Tear down here** — the rest is offline.
3. `JUDGE_URL=<frontier-base> JUDGE_MODEL=<model> JUDGE_API_KEY=<key> python3 orchestrator/judge_eval.py --frontier`
4. `python3 orchestrator/judge_eval.py --score --heuristic` (free sanity), then `... --score` (real, uses the frontier grader) → aggregate + per-category + head-to-head Gemma vs frontier.

**Chosen methodology (demoable now, rigorous later — the upgrade path is documented in
`judge_eval.py`'s header):** reference-anchored 0-5 grading (self-bias low), single grader sample,
18 queries, Claude as the intended frontier judge+grader. Rigor upgrades = a PairwiseScorer (with
blinding + position-randomization), a cross-vendor independent grader, N samples, a bigger set.
Frontier is the **baseline to beat**, never a default in the serving path.

### Chunk 2 result (2026-07-10 boot, g6e.xlarge us-east-1c on-demand, ~1h, done)
- **Contention tax = +30%** (controlled: every call forced to exactly 256 tokens via
  `ignore_eos`+`max_tokens`, 8 queries, dead-stable 15–33%). `max_solo` 12.0s → `parallel_wall`
  15.7s. **Multi-GPU (Chunk 4) is NOT justified** — a 4× box buys back at most ~30% latency. The
  first, uncontrolled run gave a noisy +9% dominated by R1's variable chain-of-thought length; the
  256-token-pinned sampler (`scratchpad/measure_controlled.py`, not committed — reproduce on any
  boot) is the trustworthy number. FP8 reasoning was fastest (5s/256tok), Gemma slowest (12s).
- **Cumulative-util scheme DISPROVEN, replaced with additive (Chunk 1 fix, now persisted).** See
  below. Booted clean afterward: all 3 `startup=1 restarts=0`, reasoning serves clean UTF-8 (no
  Ġ/Ċ), ~7.6 GiB headroom.
- **`dev_mode=true` held** — box ran ~1h on sparse interactive activity, never idle-reaped.
- **V0-engine Llama experiment FALSIFIED** (Open Q #4): jakiAJK Llama-8B AWQ under `VLLM_USE_V1=0`
  still leaks Ġ/Ċ, so the bug is not V1-detokenizer-specific. Restoring a Llama-lineage reasoner
  waits on a future vLLM or a different model/quant, not an engine flag.

**How the baseline measures it.** `fan_out_parallel` (threaded) runs a real concurrent pass;
tax = `(parallel_wall - max_solo) / max_solo`. CONTROL FOR OUTPUT LENGTH or the number is noise —
`harness.py --baseline` does not pin `max_tokens`, so R1's variable CoT dominates; the committed
harness answers the binary (not +100%) but for a trustworthy figure force a fixed token count
(`ignore_eos`+`max_tokens`, see `scratchpad/measure_controlled.py`). ~0% ⇒ timeshares fine;
~+100% ⇒ serializes. Measured: **+30%**.

**v3 status.** Four design decisions locked (see `design.md` § "v3 locked decisions"): client-side
orchestrator · pluggable judge, default in-fleet Gemma · OpenAI-compatible `model=ensemble` + debug
metadata · single-GPU baseline first, multi-GPU follow-on. Orchestrator (`orchestrator/ensemble.py`)
built + **live-smoke-verified** 2026-07-08; live harness in `orchestrator/harness.py`
(`--baseline` for the contention run, default = smoke; gateway via argv or `$CONCLAVE_GW`).

### The v3 chunk plan (breakdown + time scope)
- **Chunk 1 — pre-boot infra fixes. ✅ DONE + verified 2026-07-10.** `dev_mode` idle-stop held;
  the cumulative-util scheme was WRONG (see below) — replaced with additive per-slice utils
  (general 0.25 / coder 0.30 / reasoning 0.24) and the image pinned to `v0.24.0`, both persisted to
  `infra/variables.tf` + `user-data.sh.tftpl`. Sequential start kept (still needed).
- **Chunk 2 — contention baseline. ✅ DONE 2026-07-10.** +30% tax → multi-GPU not justified (result
  block above).
- **Chunk 3 — judge eval (the thesis payload). 🔨 HARNESS BUILT 2026-07-11; boot + scoring remain.**
  Query set + candidate cache + pluggable-scorer harness done and offline-verified (see "ONE next
  action" for the files + the 4-step run order). Remaining: one ~45min boot to `--generate`, then
  offline `--frontier` + `--score`. ~$2-3 + tiny frontier API.
- **Chunk 4 — multi-GPU. ✗ DISMISSED by the Chunk 2 baseline (+30% ≠ worth 4× cost).** Not on the
  path unless a later need (32B coder headroom, failure isolation) reopens it — then: pick box
  (g5.12xlarge vs g6e.12xlarge), per-GPU placement (`CUDA_VISIBLE_DEVICES`/pinning), measure delta.

Total remaining v3 ≈ 1-2 focused sessions (just Chunk 3). Pad boot chunks 1.5× — every boot surprises.

### Idle-stop was dev-hostile — FIXED in Chunk 1 (`dev_mode`), verify in Chunk 2
The GPU idle-stop alarm stops the box after 30 min of GPUUtil < 5%. During interactive dev — boot
debugging, restart-roulette watching, sparse requests — util sits under 5% even while you're
working, so it reaped the box mid-baseline 2026-07-08. **Fix (committed, unverified until a boot):**
`dev_mode` tfvar (`infra/variables.tf` + `gpu.tf`) widens the window to 90 min when true; default
30 min stays for unattended boots. Boot dev with `-var dev_mode=true`. Escape hatch if a session
still runs long: `aws cloudwatch disable-alarm-actions --alarm-names conclave-idle-stop-gpu
conclave-idle-stop-gpu-cpu-backstop --profile yeti-conclave`.

### Co-residency memory — RESOLVED 2026-07-10 (additive per-slice utils, NOT cumulative)
vLLM 0.24's `--gpu-memory-utilization` is a per-process REQUEST checked against FREE memory at that
process's startup: it requires `free >= util * TOTAL` and reserves that slice. Verbatim error:
`Free memory on device cuda:0 (7.19/44.39 GiB) on startup is less than desired GPU memory
utilization (0.82, 36.4 GiB)`. So the Chunk 1 "cumulative 0.25/0.55/0.82" idea was **physically
impossible** — the last starter (reasoning at 0.82) demanded 36 GiB free with only 7 left and
crash-looped forever (restarts climbed 2→6→10 on the 2026-07-10 boot). The correct scheme is
**additive per-slice, summing <~0.9**, each slice sized to weights+KV: general 0.25 / coder 0.30 /
reasoning 0.24. Sequential start (user-data waits for each `Application startup complete`) is still
required so each free-check runs against real predecessor residency. This is now the committed
default and booted clean (all 3 `startup=1 restarts=0`, ~7.6 GiB headroom). NB the 2026-07-08 v2
"verified" boot was actually running additive utils; the cumulative reorder was never tested until
it failed here. If a model OOMs on load, nudge its slice up; if one wastes headroom, down.

## v2 boot playbook (reuse for any GPU boot)

1. Re-auth: `aws sso login --profile yeti-conclave` (token expires between sessions).
2. Launch: from `infra/` — **add `-var dev_mode=true` for any interactive boot** (widens idle-stop
   to 90m so debugging doesn't get reaped; see below) —
   `terraform apply -var enable_gpu=true -var dev_mode=true -var use_spot=false -var gpu_az=us-east-1c`
   Instance create took ~10 min last successful boot.
   **NEVER run a bare apply to sweep AZs — a dry AZ HANGS, it does not error.** See
   "capacity errors stall" below. Sweep with `TF_LOG=DEBUG`, an external timeout, and a grep:

   ```sh
   TF_LOG=DEBUG terraform apply -auto-approve -var enable_gpu=true -var dev_mode=true \
     -var use_spot=false -var gpu_az=$az > /tmp/tf-$az.log 2>&1 &
   TFPID=$!
   # poll: instance up -> keep; 'InsufficientInstanceCapacity' in log -> kill, next AZ (~20s)
   grep -qm1 InsufficientInstanceCapacity /tmp/tf-$az.log && kill -TERM $TFPID
   ```
   **After ANY kill, orphan-check** — a cancelled `RunInstances` returns `context canceled`, so
   you never saw whether AWS launched a box:
   `aws ec2 describe-instances --filters "Name=tag:project,Values=conclave" --profile yeti-conclave --query 'Reservations[].Instances[].[InstanceId,State.Name]' --output text`
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
  **Utils are ADDITIVE per-slice, array order = start order: general 0.25 · coder 0.30 · reasoning
  0.24** (sum 0.79; verified boot 2026-07-10). vLLM 0.24 requires `free >= util*TOTAL` at each
  process's startup and reserves that slice — so utils are per-process requests, NOT cumulative
  ceilings. Sequential start (user-data waits for each `Application startup complete`) makes each
  free-check see real predecessor residency. Image PINNED to `vllm/vllm-openai:v0.24.0`. Each
  `--enforce-eager`. (History: a cumulative 0.25/0.55/0.82 scheme was tried and crash-looped — see
  "Co-residency memory" above.)
- **Why 14B not 32B coder:** a 32B + 2 small can't co-reside (32B+Gemma alone filled 42/44 GiB).
  32B returns in v3 on its own GPU.
- **Why Qwen-7B not Llama-8B reasoning:** the Llama distill leaks BPE byte markers (Ġ/Ċ) under
  vLLM 0.24 — reproduced across 2 quants, and the V0-engine fix is FALSIFIED (2026-07-10: jakiAJK
  Llama-8B AWQ under `VLLM_USE_V1=0` still leaks, so it's not V1-specific). Qwen tokenizer decodes
  clean. Trade: 2nd Qwen dents lineage decorrelation — restoring a Llama reasoner now waits on a
  future vLLM release or a different model/quant.
- **Cost accounting + GPU-util idle-stop:** ✅ verified 2026-07-08. Cost needs `LITELLM_LOG=DEBUG`
  (the `success_callback: ["logging"]` alone never prints `response_cost`) — now baked into user-data.
- **Gemma HF token:** real key already in SSM `/conclave/hf-token` (Gemma is gated).

## Likely landmines (pattern: nearly every boot surprises us)

- **FP8 reasoning** — booted clean at util 0.24 (2026-07-10), R1 chain-of-thought output is valid
  UTF-8, fastest member (5s/256tok — FP8 native on the L40S Ada). If it OOMs on load at a smaller
  util: raise its slice or drop `max_len`, live-swap via SSM (base64-pipe a `docker rm` + `docker
  run` with the new `--gpu-memory-utilization`) then persist to `variables.tf`.
- **Per-vLLM mem partitioning is empirical** — actual usage ran higher than nominal util last
  time. If a model OOMs on load (`Free memory ... less than desired`), lower utils and restart clean.
- **Capacity errors STALL, they don't fail (2026-07-09, cost us a session).** EC2 returns
  `InsufficientInstanceCapacity` as an HTTP 500; the AWS SDK retryer treats 500 as retryable and
  silently retries. The `timeouts { create = "3m" }` in `gpu.tf` does NOT bound it — an apply sat in
  `Still creating...` for **26 min** before we killed it. The error NEVER appears in normal terraform
  output. Diagnose only via `TF_LOG=DEBUG` + `grep InsufficientInstanceCapacity` (shows in ~20s).
  Use the fast-fail sweep in the playbook above.
- **g6e.xlarge capacity is day-volatile.** Dry in ALL of us-east-1a/b/c/d on 2026-07-09; the very
  next day (2026-07-10) us-east-1c had capacity and launched in ~20s via the sweep. AWS's "try
  us-east-1b/1c/1d" error text is generic boilerplate, not a live capacity read — don't trust it,
  sweep and measure. The fast-fail sweep (`scripts/sweep-gpu-capacity.sh`, or the playbook
  recipe) is the tool: it caught 1c's capacity immediately instead of hanging.
- **Quota is the real ceiling: G+VT = 8 vCPU** (on-demand AND spot, us-east-1). So `g6e.xlarge`
  (4 vCPU) and `g6e.2xlarge` (8 vCPU, **same single L40S 48 GB** — all `mem_util` values stay
  valid) are the only options; `g6e.4xlarge` (16 vCPU) fails `VcpuLimitExceeded`. If xlarge is dry,
  `-var gpu_instance_type=g6e.2xlarge` is a different capacity pool for ~$0.30 more on a 45-min run.
  Caveat: 8 vCPU vs 4 changes CPU-side contention — record the instance type next to any baseline number.
- **Spot is not an escape hatch here** — g6e.xlarge spot was $1.50-1.85/hr against ~$1.86 on-demand
  (2026-07-09). No real saving, plus interruption risk mid-measurement.

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
