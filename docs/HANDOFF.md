# HANDOFF — resume here

Last updated: 2026-07-12 (end of session). Read this + `design.md` to resume cold.
**v0.5→v3 all done, and the judge eval has now had its rigor pass. Nothing running, $0.
Next is v4 (MCP), or unblock the one remaining rigor item (pairwise — needs grader quota).**

## READ FIRST — the rigor pass CHANGED the v3 conclusion (2026-07-12)

The Chunk 3 headline ("Gemma ties the frontier on 15/18 — the small judge holds up") was
**too generous, and one of its caveats was flat wrong.** Full writeup:
`docs/chunk3-judge-eval-results.md`. The three things a future session must not re-derive:

1. **The old query set was too easy.** Expanded 18 → 36 with trap questions (Monty Hall,
   the 40-vs-45 average-speed trap, bare `except`). The new block discriminates **2.6×
   better** — Gemma loses 44% of trap queries vs 17% of the originals. Much of the
   original "tie" rate was the *questions* failing to separate the judges, not the judges
   being equal. **n=36 result: Gemma 0.883 ± 0.010 vs frontier 1.000, 0 win / 25 tie /
   11 loss**, weakness concentrated in **coder (0.789)**.
2. **The "grader self-bias" caveat was FALSE.** We assumed Sonnet's perfect 1.000 for its
   own judge was self-flattery. It is not: an out-of-house Gemini grader also scores the
   frontier judge **1.000** (inflation **+0.000**, n=10), and barely flatters Gemma either
   (+0.013). Cross-vendor graders agree → reference grading is *robust*, and Gemma's gap
   is **real**. Do not "correct" for a self-bias that does not exist.
3. **The real flaw is a CEILING EFFECT.** The frontier saturates at 1.000 for *both*
   graders on all 36 queries with zero variance. Reference grading cannot resolve the top
   of its scale. **Pairwise (blinded, both-orders) is the fix — it is BUILT AND TESTED but
   UNRUN**, blocked only on grader quota (below).

### 🔴🔴 READ FIRST — THE ENSEMBLE DOES NOT PAY. The v3 thesis has a negative result.

Measured 2026-07-12 (`orchestrator/divergence.py`, frozen in `eval_divergence.json`,
reproduce for $0). Nobody had ever run the baseline: **is a judged ensemble better than
just calling ONE model?** It is not.

| policy | score | cost |
|---|---|---|
| ORACLE — a *perfect* judge picking best-of-3 | 0.961 | 3× inference + perfect judgment |
| **ALWAYS coder — one model, no judge** | **0.933** | **1× inference** |
| **GEMMA-judged ensemble (the v3 design)** | **0.883** | 3× inference + judge + ~30% contention |

- **Gemma-judged ensemble − always-coder = −0.050** (95% CI [−0.107, +0.007]). The judge
  goes **backwards** vs one model, at 3× the cost.
- **ORACLE − always-coder = +0.028** (CI [+0.003, +0.052]). Even a **perfect** judge buys
  under 3 points. **That is the entire headroom of the pattern on this fleet.**

**Why: the "specialists" are not specialists.** The coder model (Qwen2.5-Coder-**14B**, the
biggest of the three) is the best candidate on **31 of 36** queries — across *all three*
categories. The fleet is one strong model and two weaker ones, not three complementary
experts. And **12/36 queries are degenerate** (all three answer equally well → no judging
task exists at all; reasoning is worst, only 3/12 diverge).

**So the next action is NOT another judge metric.** Improving the judge cannot recover
value that is not there. Options, in order:
1. **Fix the fleet, not the judge.** The pattern needs candidates that are *complementary
   and comparably strong*. Three models of similar size with genuinely different strengths
   (or different lineages/finetunes), rather than a 14B carrying two smaller models.
2. **Fix the query set.** These queries are short and general; 1/3 are degenerate. A judge
   can only be measured where candidates actually diverge.
3. Only then: select-mode / pairwise judge metrics (below). They are now *downstream* of a
   fleet that gives a judge something to do.

This is a genuine negative result and it is *worth having* — it is the kind of thing the
lab exists to find. It does NOT say "meta-reasoners over specialised outputs is a bad
pattern"; it says **this fleet has almost no diversity for a judge to exploit.**

### The ONE next action, if you still want a judge metric — `select` mode. No key, no boot.

An adversarial review (2026-07-12, three independent reviewers) found the eval **does not
measure judging**. The frontier judge sets `chosen == -1` on **34 of 36** queries — it
**ignores the candidates and writes its own answer**. So its 1.000 is mostly *"Sonnet
answers an easy question that has a gold reference"*, not judge quality — and since it is
pinned at the metric maximum, **Gemma cannot win**. "0 wins / 25 ties / 11 losses" is a
one-sided count, not a comparison.

**The cure is already in the codebase and costs nothing:** `EnsembleConfig.mode` supports
`"select"`. Run the eval with the judges forced to SELECT among the candidates (or grade
`chosen` against a per-query best-candidate label). That makes both sides actually judge,
removes the ceiling, and makes "which judge is better" answerable — **with no third-party
key and no GPU boot** (candidates are frozen in `eval_fixtures/`).

Do this BEFORE pairwise. Pairwise is a more sensitive instrument, but running it first
would just measure the wrong thing more precisely.

Other corrections a future session must not re-derive (full detail in
`docs/chunk3-judge-eval-results.md`):
- **The error bar was wrong.** `± 0.010` is grader *replication* noise. The statistic that
  bounds the claim is the paired SEM over queries: gap **0.117, SEM 0.0366, 95% CI
  [0.045, 0.188], p ≈ 0.003**. Real, but 3.2× its error bar — not 12×. `--score` now prints
  this; quote it, not the ±.
- **"Trap block discriminates 2.6× better" is NOT significant** (Fisher p = 0.146). And the
  traps aren't traps: every specialist answered every trap correctly.
- **"Self-bias was refuted" is over-claimed.** Correct: *no self-bias detected, by an
  instrument with no resolution at the ceiling.* The n=10 Gemini grades supporting it are
  **not committed** and cannot be replayed.
- **Replay is now real.** `python3 orchestrator/judge_eval.py --score` — **no env, no keys**
  — reproduces 0.883/1.000 for **$0** from a fresh clone. (It previously crashed on a clean
  checkout: nothing read `eval_fixtures/`. The defaults are now the frozen run's config, so
  the safe path is the default and you must opt IN to spend.)

### ⚠️ ALSO BLOCKED ON THE HUMAN — an OpenAI API key (for pairwise, AFTER select mode)

**Pairwise cannot run without a third-house grader key.** This is the last rigor item and
now the most valuable one, and no amount of code fixes it. Escalation: `esc-20260713-025337`.

**Why OpenAI specifically, and not just more Gemini quota.** The grader must be neutral to
*both* contestants. The frontier judge is **Anthropic** (claude-sonnet-5) and the in-fleet
judge is **Gemma — a GOOGLE model**. So Gemini is *not* an independent grader: it shares a
house with the local judge and biases toward our own thesis, which is the direction a
skeptic attacks first. **OpenAI is the only available third house**, neutral to both — it
removes the `--bracket` bounds workaround entirely and gives a single clean number.
(Billing on the Gemini key would lift the quota but would NOT fix the bias; it only buys
the upper-bound arm of the bracket.)

**When the key arrives — no boot needed, ~5 minutes, ~$0.10:**
```sh
# 1. store it (run in YOUR OWN terminal — do not paste a key into a Claude session)
aws ssm put-parameter --name /conclave/grader-api-key --type SecureString --overwrite \
  --value 'sk-...' --profile yeti-conclave

# 2. run pairwise. GRADER_* = the neutral grader; JUDGE_* = the frontier judge being graded.
export JUDGE_URL=https://api.anthropic.com JUDGE_MODEL=claude-sonnet-5 \
  JUDGE_API_KEY=$(aws ssm get-parameter --name /conclave/judge-api-key --with-decryption \
    --profile yeti-conclave --query Parameter.Value --output text)
export GRADER_URL=https://api.openai.com GRADER_MODEL=gpt-5.2 \
  GRADER_API_KEY=$(aws ssm get-parameter --name /conclave/grader-api-key --with-decryption \
    --profile yeti-conclave --query Parameter.Value --output text)
python3 orchestrator/judge_eval.py --score --pairwise --save
```
`_grader_bias()` will now return `None` (neutral), so `--pairwise` stops refusing. Candidates,
judgments and the grader memo are all frozen in `eval_fixtures/` — **the GPU stays off.**

**What pairwise is expected to settle:** the ceiling effect (#3 above). Reference grading
pins the frontier at a saturated 1.000 and cannot measure its headroom over Gemma. Pairwise
is blinded and grades BOTH orders, so position bias is cancelled and *reported* (any flip
lands in `diagnostics.position_flips`) rather than hiding in the variance.

### The ONE next action (pick)
- **(a) Unblock pairwise** — see the blocked-on-the-human block above. Needs the OpenAI key.
- **(b) v4 — MCP front-end.** An MCP server as the structured interface to the platform;
  the OpenAI-compatible gateway already makes any such client first-class.

### Judge-eval harness — what changed, and 3 landmines it defused
`--generate` [boot] / `--frontier` [offline] / `--score [--pairwise|--bracket|--heuristic]`
[offline]. **Re-scoring costs $0** — the grader memo (`GradeCache`) is committed, so
`--score` replays from cache with zero API calls. Env: `JUDGE_*` = the frontier judge under
comparison; `GRADER_*` = the grader; `GRADER_SAMPLES` = N samples (>1 gives error bars).

Three **pre-existing bugs in committed code**, found only by running it rigorously:
- **`claude-sonnet-5` now REJECTS `temperature`** ("deprecated for this model"). The old
  `frontier_call` hardcoded `temperature=0` → **`--frontier` was already broken**. Now
  retries without it on rejection.
- **No retry anywhere.** A bracket run is 200–400 sequential calls; runs died at 4:41 and
  5:26 to one transient 503/429 and lost everything. Now backs off honoring `Retry-After`
  and **paces under RPM caps** — bursting into a per-minute cap and backing off is the
  wrong shape.
- **`judge_over_cache` re-judged everything** → growing the query set would have silently
  **rewritten the frozen 18's judgments**, breaking comparability and voiding every cached
  grade. Now incremental.

**Grader bias is a VENDOR property, not a hostname one.** A `grader_host != judge_host`
check waves through a Gemini grader even though Gemma is Google's. `_grader_bias()` encodes
this; `--pairwise` **refuses** a colliding grader (open pairwise has no reference to anchor
the bias against), and `--bracket` runs both biased graders as explicit **bounds**.
Vendor matching is **suffix-based** (`*.googleapis.com` catches Gemini-via-Vertex too), and
a host whose house cannot be established (a reseller) returns **`UNVERIFIED`** — *unknown is
not unbiased*, and `--pairwise` refuses that as well. **`api.openai.com` reads as neutral**,
so the OpenAI key above will pass the guard cleanly.

### Two harness behaviours a future session WILL hit (from the PR #9 code review)
- **A judge call that FAILS is not cached.** `run_judge` degrades to a raw specialist answer
  on any exception (right for serving, catastrophic for an eval). If a phase prints
  `JUDGE FAILED on <qid> ... not cached`, **re-run that phase** — the row is deliberately
  absent so it gets retried, not frozen in as a fake judgment.
- **`--score` only scores queries EVERY judge answered.** If you grow `QUERY_SET` and run
  `--generate` but forget `--frontier`, the new queries are **skipped** (with a loud warning
  and a `skipped_unjudged` list in the report) rather than scored 0.0 for the missing judge.
  A shrinking `n=` in the report header is the tell: run the missing phase.
- Swapping `JUDGE_MODEL` **drops** prior judgments from the old model and re-judges them, so
  a judgments file never silently mixes two judges.

## Where we are

- **v0.5 (cost layer):** ✅ done + verified.
- **v1 (single 70B):** ✅ done + verified. Qwen 2.5 72B AWQ served tokens over Tailscale, torn down.
- **v2 (gateway + 3-model fleet):** ✅ **done + verified end-to-end** (2026-07-08 boot on
  g6e.xlarge on-demand, us-east-1c). All 3 (coder/reasoning/general) served CLEAN through the
  LiteLLM gateway — reasoning had NO byte-marker leak (Qwen distill fix holds). Per-model cost
  accounting prints (`response_cost` matched configured $/token exactly). GPUUtil idle-stop metric
  flowed. Torn down, $0 spend. One landmine hit + fixed (see below), fixes committed.
- **v3 (ensemble + judge):** ✅ **built, and now rigorously measured (2026-07-12).** Chunk 1 fixed +
  Chunk 2 done (2026-07-10) + Chunk 3 judge eval (2026-07-11) + **rigor pass (2026-07-12, see the
  READ FIRST block above — it revised the conclusion downward)**; Chunk 4 dismissed. The core v3 arc
  is complete. Remaining: pairwise (quota-blocked) or v4 (MCP front-end).

### Chunk 3 result — judge eval (2026-07-11, the v3 thesis) — **SUPERSEDED, see READ FIRST above**
Kept for the record. "Does the in-fleet Gemma-9B judge hold up vs a frontier judge (Claude Sonnet 5)?"
The original answer was **"yes"**: 18 queries (6/6/6), reference-anchored grading, **Gemma 0.89–0.91
vs frontier 1.00, tied on 15/18**, trailing only on code (0.77). **The rigor pass showed this was too
generous** — the query set was too easy (the tie rate collapses to 25/36 on harder questions), and the
"grader self-bias" caveat it leaned on turned out to be **false**. The local heuristic (keyword) says
frontier 0.69 vs Gemma 0.37 — an ARTIFACT (it scores phrasing-overlap with the reference, not
correctness); it's a CI backstop, not the number.

**Harness (built + committed this chunk):** `orchestrator/eval_queryset.py` (now 36 labeled Qs),
`candidate_cache.py` (one boot caches candidates, eval iterates offline), `judge_eval.py` (pluggable
Scorer: LocalHeuristic + ReferenceGrader; provider-agnostic keyed judge/grader; phases `--generate`
[boot] / `--frontier` / `--score`). Frozen run + report in `orchestrator/eval_fixtures/` — re-score
offline with `judge_eval.py --score`. Frontier judge/grader = Anthropic's OpenAI-compat endpoint
(`https://api.anthropic.com`); it rejects `response_format=json_object`, so `frontier_call` strips it
and relies on prompt + lenient parse (key in SSM `/conclave/judge-api-key`).

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
- **Chunk 3 — judge eval (the thesis payload). ✅ DONE 2026-07-11.** Gemma judge 0.89–0.91 vs frontier
  1.00, tied 15/18 → a small self-hosted judge holds up. Full result + caveats above and in
  `docs/chunk3-judge-eval-results.md`. Harness committed; frozen run in `orchestrator/eval_fixtures/`.
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
