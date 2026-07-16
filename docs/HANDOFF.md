# HANDOFF — resume here

> # ✅ 2026-07-16 (latest) — PREDICTABILITY MEASURED. A query-only router does NOT pay on QUALITY.
>
> **The gate the last handoff named is CLOSED.** `orchestrator/router_predictability.py` (pure
> stdlib, $0, offline — reads the committed `eval_pairwise_modern2_hard.json`). Replay:
> `python3 orchestrator/router_predictability.py` (`--demo` self-checks the LOO math).
>
> **The question:** can a cheap query-only picker predict the pairwise per-query winner better
> than a constant "always call model X"? Feature = query CATEGORY (code/reason/general — the one
> signal the hard set is organized by). Honest scoring: **leave-one-out** (mode from the other 29),
> and the baseline is the BEST constant, not the weakest.
> ```
>                          Metric-1 (decided, n=22)   Metric-2 (ties-free, n=30)
> always qwen3  (top MEAN pts)     27.3%                      46.7%   <- last handoff's baseline
> always mistral (most WINS)       45.5%                      60.0%   <- the REAL bar
> category router (LEAVE-ONE-OUT)  45.5%                      60.0%   <- ties it. +0.0%
> oracle (perfect per-query pick)  100%                       100%
> ```
> **The +18pt "win" over always-qwen3 is a WEAK-BASELINE ILLUSION.** qwen3 tops the round-robin
> *mean* but rarely wins a query outright (6/22); mistral wins on *count* (10/22). Against the
> honest baseline — **always call mistral** — the query-only router captures **exactly nothing**.
> Within-category the winners are near-random (`hardreason` = **0% LOO**, and 6/10 ties anyway):
> the category feature just re-discovers "mistral is strong on technical queries."
>
> **VERDICT: the pairwise oracle ceiling is NOT capturable from the query alone beyond a constant
> pick. A router does not pay on QUALITY.** This is consistent with the prior caveat — the variation
> is among already-5/5 answers, so it looks like grader noise, not routing signal. The router's
> **only** surviving case is the one the last handoff flagged: **cost/latency (1 call, not 3).**
>
> ### ⚠️ SCOPE OF THE CLAIM (don't over-read the null)
> This bounds the CATEGORY router, not *all* query-only routers — a richer feature (embeddings,
> keywords, length) is untested. But within-category randomness is weak evidence any finer feature
> helps. And **n=22 decided queries is THIN** (~binomial ±10pts): 27% vs 45% is real, 45% vs 45%
> is a genuine tie. Do not build a classifier on this without more queries.
>
> ### ➡️ NEXT — the fork the quality-null forces
> 1. **Ship the router as a COST play, not a quality play.** Route by category to save 3×→1× calls
>    at ~zero quality loss (the null IS the license: mistral-default costs nothing vs the oracle).
>    Honest framing: it's a cost optimization with a measured quality floor, not a quality win.
> 2. **OR push quality:** bigger/harder query set to thicken n and unsaturate further, and/or a
>    richer query-only feature — only if the cost play isn't the goal. The instrument is built; this
>    is just more labels through the same $0 pipe.
> 3. **Judge stays PARKED** (unchanged) — a null on query-only *routing* says nothing revives
>    fan-out+judge, which pays 3× to pick *after* generating.

> # ✅ 2026-07-15 — PAIRWISE RESOLVED THE SATURATION. The ROUTER has a real signal.
>
> **All merged to `main`** (PRs #11 modern fleet, #12 pairwise). Everything replays from
> `eval_fixtures/` for **$0**.
>
> **The problem pairwise fixed:** absolute reference grading topped out — **20/30 modern-fleet
> queries were exact 5/5 ties**, so the +0.040 headroom was *saturated* and blind to which strong
> model was actually better. `fleet_pairwise.py` round-robins the 3 models (blinded, both orders,
> position-debiased, reusing `PairwiseScorer`; grader **gpt-5.2**, neutral to Alibaba/Google/
> Mistral; 180 calls, $0 GPU).
> ```
> round-robin (mean pts/query, max 2): qwen3 1.117 · mistral 0.967 · gemma3 0.917
> clear pairwise winner 22/30 (only 8 true ties, down from 20 absolute)
> per-query wins: mistral 10 · qwen3 6 · gemma3 6
> on the 20 absolute "ties": mistral 6 · qwen3 4 · gemma3 2 → SPLIT (top only 50%)
> position flips 11/90 (12%) — reliable
> ```
> **The absolute headroom UNDERSTATED the routing signal — it was ceiling-limited.** The "ties"
> hid real per-query variation across all 3 models. qwen3 stays the strongest *default*, but
> different models genuinely win different queries → **a router has a signal to exploit.**
>
> ### ⚠️ THE HONEST BOUND — read before building the router
> This is an **ORACLE-router ceiling** (post-hoc best pick). A REAL router picks from the QUERY
> ALONE, so it captures only the **predictable** part of this variation — **untested, and it is
> the first thing building the router must measure.** And the variation is among already-good
> (5/5) answers, so the *quality* gain is modest; the router's surer win is **cost/latency** (1
> call, not 3). Does NOT revive fan-out+judge (a judge pays 3× to pick *after* generating).
>
> ### ➡️ THE NEXT STEP — build the router, measure PREDICTABILITY
> Can a cheap **query-only** picker (small classifier, or query features) predict the per-query
> winner better than "always qwen3"? That number says how much of the pairwise oracle ceiling a
> real router captures. Replay the signal: `MODERN_FLEET=modern2 CONCLAVE_QUERYSET=hard <gpt-5.2
> grader env> python3 orchestrator/fleet_pairwise.py`. Per-query winners are in
> `eval_fixtures/eval_pairwise_modern2_hard.json` — that IS the router's training label.
>
> ### The architecture the discovery settled on
> **diagnostic (`divergence.py`) → operational (router) → continuous fitness monitor** (divergence
> run on a schedule vs live traffic + candidate new models: flag drift, vet swaps). **Judge is
> PARKED with a trigger:** revisit only if the model landscape re-diverges (genuinely specialized
> or deliberately-diverse models emerge) — convergence makes judging pay *less* as models improve.

> # ✅ 2026-07-14 — THE MODERN FLEET IS BUILT. Route-don't-judge CONFIRMED on it.
>
> **A working PRIVATE fleet: **Qwen3-32B-AWQ / Gemma-3-27B-FP8
> / Mistral-Small-3.2-24B-FP8**, one per L40S card, served **Tailscale-only** (no public ports,
> no SSH tunnel). Reproducible; `runpod/boot.sh` handles CUDA-13→vllm-0.24. Everything below
> replays from `eval_fixtures/` for **$0**:
> `MODERN_FLEET=modern2 CONCLAVE_QUERYSET=hard python3 orchestrator/divergence_modern.py`.
>
> ### The result — the ideal fleet, and a judge STILL doesn't pay
> ```
> qwen3   0.935   each model wins EXACTLY ONE category (qwen3 coder / gemma3 reasoning /
> gemma3  0.911   mistral general) — comparable strength (within 0.028, statistically tied),
> mistral 0.907   genuine complementarity, 3 lineages = the textbook ideal ensemble regime.
> ORACLE  0.976
> HEADROOM +0.040  CI [+0.012, +0.068]  NOT RESOLVED, leaning not-worth-it
> JUDGE (gemma3 select) 0.920  <  best-single 0.935   ← NEGATIVE capture
> ```
> **Even the ideal modern fleet does not benefit from a self-hosted judge.** Route, don't judge,
> now confirmed on the GOOD fleet — not the quota-crippled old one. Fixing qwen3 (stronger)
> *lowered* headroom (+0.056→+0.040): the literature's **convergence** effect, live.
>
> ### 🔧 qwen3 CONFOUND — fixed, keep in mind for any reasoning model
> The FIRST modern run scored qwen3 at 0.764 and nearly called it dead weight. Artifact:
> `max_tokens=1024` let its `<think>` block eat the budget, truncating the answer that got
> graded. With **2048 tokens + `chat_template_kwargs:{enable_thinking:false}`** it is **0.935**
> — the strongest single. Reasoning models need budget and/or thinking-off for a fair grade.
> `eval_candidates_modern_hard` (handicapped) is kept only so the confound is auditable;
> `modern2` is the real fleet.
>
> ### 🖥️ Two hard-won infra facts
> - **The driver is a PER-MACHINE lottery, not per-GPU-type.** L40S seen with 550/570/580 this
>   session; a $2.99 H100 shipped an OLDER driver than a $0.39 L4. **Deploy cheap, `nvidia-smi`
>   FIRST, terminate + re-roll if CUDA<13** (vllm 0.24 needs CUDA-13/driver-580). Do NOT pay up.
> - **Tailscale on RunPod works** (userspace mode — no `/dev/net/tun`): `tailscaled
>   --tun=userspace-networking` + `tailscale up --authkey` (ephemeral key in SSM
>   `/conclave/tailscale-authkey`) + `tailscale serve --http=PORT http://localhost:PORT`. Laptop
>   reaches it by MagicDNS name (`http://conclave-<name>:PORT`). **Run the orchestrator POD-SIDE**
>   (scp `orchestrator/` + grader key), NOT over an SSH tunnel — a dropped tunnel looks identical
>   to an idle GPU and the watchdog correctly reaps the pod.
>
> ### ➡️ NEXT (all optional; nothing running, verdict is DIRECTIONAL not resolved)
> 1. **Harder query set** — 25/30 are at the grader ceiling, so the verdict "leans" not-worth-it
>    rather than resolving. A harder set unsaturates these stronger models and settles it. $0 GPU
>    to write; one boot to generate.
> 2. **Build the ROUTER** — the capability the data actually supports (pick the right model per
>    request, don't fan out and vote).
> 3. **More judges** — only gemma3-select was tested; qwen3-as-judge or synthesis mode could
>    differ (though Self-MoA already showed judges often capture negative value).
> The reusable asset is `divergence_modern.py` + `divergence.py` — point them at any new fleet's
> candidates to get a $0 headroom verdict BEFORE building a judge.

> # ✅ 2026-07-14 (earlier) — THE RETRACTED CLAIM IS NOW MEASURED, AND IT HOLDS.
>
> **Branch: `selfmoa-honest-judge`.** Everything below replays from the committed fixtures for
> **$0** (`CONCLAVE_QUERYSET=hard python3 orchestrator/selfmoa_judge.py --mode select`).
>
> ### The headline, on a sound instrument
> ```
> baseline (temp-0 coder, ONE sample)        0.696
> SELF-MoA SELECT over 8 samples             0.767
> >>> gain +0.0711   95% CI [+0.024, +0.118]   <- excludes zero, under t
> ```
> **Every defect that voided the old +0.058 is closed, and the number came back BIGGER:**
> | defect | fix |
> |---|---|
> | judge WAS the grader | judge = **`gpt-5.2-2025-12-11` (OpenAI)**, grader = `claude-sonnet-5` (Anthropic). Different houses. |
> | baseline graded at 3, arms at 1 | both at **`GRADER_SAMPLES=3`**. The mismatch guard hard-exits otherwise. |
> | judge ignored the candidates | it **selected on 29/30**. |
> | grading provenance unrecorded | the report now persists `grader: {model,url,samples}` — in the shape the guard reads. |
>
> Truncation (`MAX_CANDIDATE_TOKENS=6000`) hit 6/30 queries, biasing **against** the judge — so
> +0.071 is **conservative**.
>
> ### The oracle was re-graded at samples=3, and the N-confound is now decomposed
> ```
> ORACLE@k:  @1 0.701 · @2 0.760 · @3 0.783 · @4 0.796 · @5 0.804 · @6 0.811 · @7 0.816 · @8 0.820
> ```
> **AT MATCHED CANDIDATE COUNT (k=3): self-MoA 0.783 vs the 3-model fleet's oracle 0.722
> = +0.0606, 95% CI [+0.018, +0.103].** *Sampling ONE model 3× reaches a higher ceiling than
> running 3 DIFFERENT models, with the candidate count held FIXED.* The old, confounded +0.0977
> is still printed but **explicitly marked**: +0.0371 of it was bought by nothing but extra draws.
> `selfmoa.py` now **refuses** to state an oracle-relative statistic unless the oracle was graded
> exactly as the run is graded.
>
> ### 🔴 STALE FIXTURES WERE SHIPPING RETRACTED NUMBERS TO ANY FRESH CLONE — fixed
> `eval_divergence_hard.json` still said **`verdict_resolved: TRUE`** off the withdrawn *z*
> interval. Regenerated under *t*: **`ci95 [0.0025, 0.0509]`, `verdict_resolved: FALSE`** — the
> 0.05 threshold lies inside the interval, exactly as the retraction said. The working copies are
> **gitignored**, so the corrected artifacts existed only on one laptop. They are now committed.
>
> ### Keys (changed this session)
> - `/conclave/grader-api-key` → **OpenAI** (`sk-proj…`). Restricted key: *Model capabilities =
>   Write*, *Models = Read*, everything else None. **This is the current grader** (gpt-5.2).
> - `/conclave/gemini-api-key` → the **Gemini** key, moved off the grader slot (hash-verified).
> - `/conclave/judge-api-key` → **Anthropic** (`claude-sonnet-5`). Now the **secondary grader** —
>   only the early self-MoA / old-fleet work used it (~$9.99 over 3 days), and those replay for
>   **$0** (cached). **Modern grading is `gpt-5.2`/OpenAI**, so default to that for anything new
>   (neutral to the current fleet, and the number everything modern is already on). This key is
>   kept **funded-but-small on purpose — a capped enabler**: enough credit that a run never blocks
>   on a top-up, little enough that it can't run away. Do NOT let it drain to $0 (it becomes a
>   blocker) OR over-fund it (it becomes a runaway risk); a small standing balance is the design.
>
> ### 💡 The cost-safety principle (one model across all surfaces)
> **Prepaid caps everywhere — the ceiling lives OUTSIDE the thing being capped.** The RunPod
> credit balance, the AWS budget hard-stop, and the API grader keys are the *same* mechanism, not
> three separate hacks: fund each just enough to enable work, capped low enough to bound a
> runaway. Keep balances small on purpose. This is why `boot.sh` fails closed, why the RunPod
> balance is kept small, and why the grader keys are funded-but-small.
> - **`gpt-5.5` REJECTS `temperature` outright.** `gpt-5.2` accepts it at 0 and 0.3. The eval
>   *depends* on that knob (temp 0 = reproducible single grade; 0.3 = probes grader spread), so
>   **pin `gpt-5.2-2025-12-11`** — a dated snapshot, never a `-latest` alias, or the frozen
>   GradeCache silently stops being comparable.
>
> ### ✅ THE GEMMA SYNTHESIZE ARM RAN. The v3 "small in-fleet judge" thesis DOES NOT PAY.
> In-fleet Gemma-2-9B judging the 8 frozen coder candidates. Grader = claude-sonnet-5 (different
> house), samples=3. **All replay from `eval_fixtures/` for $0.**
> ```
> solo       (Gemma alone, NO candidates)   0.522
> synthesize (Gemma + 8 candidates)         0.653   CI vs baseline [-0.092, +0.008]
> select     (Gemma picks one)              0.673   CI vs baseline [-0.108, +0.063]
> baseline (coder, one sample)              0.696
> gpt-5.2 select                            0.767   <- the ONLY judge that beat baseline
> ORACLE@8                                  0.820
> ```
> **The pre-registered "synthesize beats 0.820" prediction is FALSIFIED.** A weak aggregator does
> not beat strong candidates — synthesize (0.653) lands *below* baseline (0.696). But the **solo
> ablation** shows this is NOT "the judge ignored the candidates": `synthesize − solo = +0.131`,
> so Gemma read the material and improved on its own answer by 13 points — it just cannot
> synthesize strong candidates into something that beats the best of them. And `select − synthesize
> = +0.020`: for a weak judge, **picking beats synthesizing**. **Only a strong judge (gpt-5.2,
> +0.071) captured value.** A small self-hosted meta-reasoner does not earn its keep at this scale.
> **This is the v3 thesis, answered — negatively, on a sound instrument.**
>
> ### 🎰 THE DRIVER IS A PER-MACHINE LOTTERY, not a per-GPU-type property. Do NOT pay up to fix it.
> Seen across five boots this session: **L4 → 580/CUDA13, 570/CUDA12.8, 550/CUDA12.4; H100 →
> 570/CUDA12.8.** The $2.99 H100 shipped an OLDER driver than a $0.39 L4. So: **deploy the cheap
> card, `nvidia-smi` FIRST, terminate and re-roll if CUDA<13.** `boot.sh` now installs the matched
> vLLM either way (CUDA≥13 → 0.24; 12.x → 0.11+transformers<5), but for comparability with the
> frozen candidates you want **CUDA 13 / vllm 0.24** — re-roll a cheap L4 until you get driver 580.
>
> ### 🔌 RUN THE ORCHESTRATOR POD-SIDE, not over an SSH tunnel.
> A dropped tunnel looks *identical* to an idle GPU: the judge calls fail, Gemma goes to 0%, and
> the idle watchdog correctly reaps the pod mid-experiment (it did, once). Fix: `scp` the
> `orchestrator/` dir + `eval_fixtures/` + the grader key onto the pod and run there against
> `http://127.0.0.1:8003`. No tunnel, and the pod stays busy so the idle rule never fires. The
> recipe is `/workspace/run_arms.sh` (rebuild it — it was pod-local).
>
> ### (historical) The first Gemma boot FAILED — `boot.sh` had three bugs, now all fixed.
> Cost of the failed boot: **$0.43.** Nothing is running; RunPod balance **$8.02**, zero pods.
> Three real bugs found, all now fixed and committed:
> 1. **`boot.sh` never installed vLLM.** It assumed the image had it (the H100 boot had it
>    pip-installed *by hand*). Every server died `ModuleNotFoundError: No module named 'vllm'`.
> 2. **THE DRIVER RULE IN THIS FILE WAS WRONG.** It said "driver ≥ CUDA 12.8". An **L4 WITH CUDA
>    12.8** was rejected: *"The NVIDIA driver on your system is too old (found version 12080)"*.
>    vllm 0.24 is built on torch 2.11+**cu130** and links `libcudart.so.13` → it needs **CUDA
>    13.0 / driver ≥ 580**. Swapping torch to cu128 does **not** help (vllm's own `.so` still
>    wants cu13). `boot.sh` now reads the host CUDA and picks: **≥13 → vllm 0.24.0**;
>    **12.x → vllm 0.11.0 + `transformers<5`** (vllm 0.11 declares `transformers>=4.55.2` with no
>    upper bound; pip takes 5.x; 5.x dropped an attribute vllm reads → *"GemmaTokenizer has no
>    attribute all_special_tokens_extended"*).
> 3. **A dead fleet reported SUCCESS.** A model that failed to start was a *warning*, and
>    `.fleet-ready` was touched **unconditionally** — so a boot where nothing loaded armed the
>    idle rule and left a GPU billing with nothing serving. Now: `kill_pod` + exit, flag never
>    written.
>
> **RTX 4090 / RTX 4000 Ada were capacity-dry on RunPod; only the L4 deployed.** If you want the
> frozen fleet's exact stack (vllm 0.24), you need an **H100** — it is the only card we have seen
> ship driver 580.
>
> ### ➡️ THE ONE NEXT ACTION (unchanged in substance, now unblocked)
> **Boot Gemma and run `--mode synthesize`.** It is the only untested mechanism and the only one
> that can exceed the ORACLE@8 = **0.820** ceiling (selection is *bounded* by it; synthesis is
> not). The frontier comparator now exists at the SAME truncation budget: **gpt-5.2 select =
> 0.767**. Run `--mode select` with Gemma too — that is the control.
> ```sh
> # fleet_gemma.json = Gemma ALONE (mem_util 0.6 — 0.25 of a 24GB card starves the KV cache)
> FLEET_JSON=/workspace/runpod/fleet_gemma.json MAX_LIFETIME_MIN=120 IDLE_MIN=25 bash boot.sh
> export JUDGE_URL=http://localhost:18003 JUDGE_MODEL=general JUDGE_API_KEY=none
> export GRADER_URL=https://api.anthropic.com GRADER_MODEL=claude-sonnet-5 GRADER_SAMPLES=3
> export MAX_CANDIDATE_TOKENS=6000 CONCLAVE_QUERYSET=hard
> python3 orchestrator/selfmoa_judge.py --mode select      # control: vs gpt-5.2's 0.767
> python3 orchestrator/selfmoa_judge.py --mode synthesize   # CAN IT BEAT 0.820?
> ```
> **AWS quota was approved: on-demand G+VT is now 64 vCPU** (was 8; spot still 8). That reopens a
> 32B coder and multi-GPU — but **quota was never the blocker, CAPACITY was**, and g6e is still
> dry. Stay on RunPod; keep the Terraform as an unmaintained fallback.

> # 🔴🔴 RETRACTIONS — 2026-07-14, from the code review on PR #10. READ BEFORE QUOTING ANY NUMBER.
>
> Two headline claims from this session **do not survive review and are WITHDRAWN.** A third is
> **restated at a third of its advertised size.** Every error ran in the same direction: making
> the result look better than it was.
>
> ### ❌ RETRACTED: "Self-MoA — the judge gains +0.058, the pattern PAYS."
> **The number is VOID.** Three independent defects, any one of which sinks it:
> 1. **The baseline was graded differently from the thing compared against it.** To save money
>    the Self-MoA grading ran at `GRADER_SAMPLES=1`; the baseline (0.696) came from the
>    divergence run at `GRADER_SAMPLES=3`. **A single noisy grade was compared against a
>    mean-of-three.** A mean-of-3 is variance-reduced and sits ~0.011 *below* its own
>    single-grade counterpart on identical answers — so the baseline was depressed *by
>    construction* while the oracle (a MAX) was inflated by full-noise grades. On a matched
>    baseline: **gain +0.047, CI [−0.005, +0.099] — CROSSES ZERO** (under z, t *and* bootstrap).
> 2. **The judge WAS the grader.** The run exported only `GRADER_*`, so `JUDGE_*` silently fell
>    back to it: `claude-sonnet-5` chose the answer and then `claude-sonnet-5` graded the answer
>    it had chosen. It picks what it will score highly.
> 3. **The guard written to catch (2) was dead code** — `judge_is_grader` was computed and then
>    only ever read *inside* `if ignored_candidates:`, so a judge that selected properly while
>    *being* the grader sailed through with no warning.
>
> **The Self-MoA gain is UNMEASURED, not positive.** A defensible number needs a judge that is
> not the grader (→ the in-fleet Gemma run, below) and matched grader configs.
>
> ### ❌ RETRACTED: "the verdict is now RESOLVED."
> `divergence.py` used **z = 1.96 with an estimated sigma** at n=30, on paired diffs that are
> **24/30 exact zeros** — badly anti-conservative. With the correct **t** quantile (df=29,
> 2.045) the upper bound is **0.051**, so the 0.05 threshold falls **inside** the CI. Bootstrap
> agrees. **Neither query set resolves the verdict:**
> ```
> hard (n=30):  +0.0267  CI [+0.003, +0.051]  -> NOT RESOLVED
> base (n=36):  +0.0278  CI [+0.002, +0.053]  -> NOT RESOLVED
> ```
>
> ### ⚠️ RESTATED: "ORACLE@8 (0.813) beats the whole fleet's oracle (0.722) by +0.091."
> **Confounded by N and by grading.** The +0.091 decomposes *exactly*:
> `+0.039` (max-over-8 vs max-over-3 — just more lottery tickets) `+ 0.018` (single-grade vs
> mean-of-3 on the fleet side) `+ 0.034` (**the real, matched effect**). At matched N and
> matched grading, the coder's own oracle@3 is **0.775** vs the fleet's **0.740**.
> **The direction survives; the magnitude was overstated 2.6×.** The oracle@k curve
> (`@1 0.695 · @2 0.752 · @3 0.775 · @4 0.787 · @8 0.813`) is a textbook winner's curse and was
> nowhere acknowledged.
>
> ### ✅ WHAT SURVIVES (and it is the qualitative core)
> - The ceiling collapsed **86% → 20%**. The instrument is genuinely unsaturated now.
> - Headroom did **not** move: **+0.028 → +0.027**. The ceiling was hiding nothing.
> - Disagreement **tripled** (ties 78% → 40%; divergent 67% → 80%) while headroom stayed flat —
>   **divergence is not headroom.**
> - **The fleet is HIERARCHICAL**: the coder wins **all three categories**, beating the *reasoner*
>   at reasoning (0.900 vs 0.807) and the *general* model at general (0.500 vs 0.440), on a
>   balanced 10/10/10 split. **The fleet has no specialists. Parameter count beats
>   specialization.** This does not depend on any confidence interval.
>
> **All defects are fixed in code and verified by execution:** the `judge == grader` guard now
> **fails fast before a single paid call**; a `grader-config mismatch` guard refuses to emit a
> number when the baseline and the run are graded differently; all intervals use **t**; VOID is
> **persisted to the JSON** (it was print-only, so a void run wrote a clean-looking number);
> select-mode grades the **original** sample, not the truncated one; and `runpod/watchdog.sh`
> had **no key fallback** — with no `/workspace/.runpod_key` it would 401, `pkill` the fleet, and
> **exit 0 while the GPU kept billing.**


Last updated: **2026-07-13 (end of session).** Read this + `design.md` to resume cold.
Nothing running. No instances. **$0 spent this session.**

> ## ✅ PR #9 IS MERGED. `main` is now CORRECT and current. Work continues on `v3-hard-queries`.
>
> The old warning that "main is stale and wrong" is **resolved** — PR #9 merged 2026-07-13, so
> `divergence.py` and the corrected docs are on `main`. Current branch: **`v3-hard-queries`**
> (branched off `origin/main`, one commit: `2e03a0c`).
>
> **Local-git oddity, already diagnosed, NOT a problem:** local `main` and `origin/main` have
> **no common ancestor** (git refuses to fast-forward). The remote history was rewritten at some
> point — same commit messages, same author dates, different SHAs. `origin/main` is a content
> superset of local main. If local `main` annoys you: `git reset --hard origin/main` (back it up
> first). Nothing is lost.

> # ~~🎯 THE PATTERN PAYS — from SAMPLING, not from a fleet~~ 🔴 **RETRACTED — SEE THE TOP OF THIS FILE**
>
> **EVERY NUMBER IN THIS BLOCK IS VOID.** Kept only so the retraction has something to point at.
> The judge was the grader; the baseline was graded at 3 samples while the arms used 1; the
> oracle comparison is confounded by sample count. **Do not quote any of it.**
>
> | | candidates | judge | vs no judge |
> |---|---|---|---|
> | **FLEET** (the v3 design) | 3 models × 1 sample | 0.883 | **−0.050** ← *this one stands* |
> | ~~**SELF-MoA**~~ | ~~1 model × 8 samples~~ | ~~0.753~~ | ~~+0.058~~ **VOID** |
>
> ```
> baseline   temp-0 coder, one sample     0.696   <- graded at 3 grader samples
> ORACLE@8   the best of 8 samples        0.813   <- graded at 1. NOT COMPARABLE.
> ```
> The matched, defensible statement is only this: **sampling one model 3× (oracle@3 = 0.775)
> beats the 3-model fleet's oracle (0.740) at matched N and matched grading — +0.034, not
> +0.091.** Whether a *real judge* can capture any of that is **UNMEASURED**.
>
> ### ⚠️ SYNTHESIZE MODE IS VOID — and it is a trap this project fell into TWICE
> It scored **1.000** and was nearly reported as a triumph. It is worthless: the judge **wrote
> its own answer on 29/30 queries** (`chosen == -1`) — it ignored the samples entirely — **and
> the grader is the SAME MODEL as the judge** (`claude-sonnet-5`), so it marked its own homework.
> This is the **same artifact already retracted once** (the base run's frontier judge did
> `chosen == -1` on 34/36). `selfmoa_judge.py` now **detects and refuses it**.
> **A synthesis judge must be NO STRONGER than the candidates** (or it has no reason to read
> them), **and the grader must be a different house than the judge.**
>
> ## 🔬 THE ONE NEXT ACTION — SYNTHESIS with the IN-FLEET judge
>
> **The only untested mechanism, and the only path that can exceed the 0.813 ceiling.**
> Selection is *bounded* by ORACLE@8 = 0.813. **Synthesis is not** — its output need not be any
> candidate, so it can in principle beat the best sample. Nobody has measured it here.
>
> **The design (both guard conditions satisfied — this is why it is valid where the last one was VOID):**
> | | | why |
> |---|---|---|
> | **judge** | **in-fleet Gemma-2-9B** (`general`) | **WEAKER than the coder**, so it cannot simply out-answer the candidates — it has to *read* them. That is what makes it a real synthesis test. |
> | **grader** | **claude-sonnet-5** (Anthropic) | **A DIFFERENT HOUSE from Gemma (Google)**, and **≠ the judge**. Nothing marks its own homework. |
> | **candidates** | **the 8 FROZEN coder samples** | Already committed in `eval_fixtures/`. **One variable changes: the judge.** |
>
> **Run BOTH modes** — the comparison is the point:
> ```sh
> # boot ANY small Ada/Hopper GPU (driver >= CUDA 12.8) and load ONLY gemma (~6GB):
> #   --model hugging-quants/gemma-2-9b-it-AWQ-INT4 --served-model-name general --port 8003
> export JUDGE_URL=http://localhost:18003 JUDGE_MODEL=general JUDGE_API_KEY=none
> export GRADER_URL=https://api.anthropic.com GRADER_MODEL=claude-sonnet-5 \
>   GRADER_API_KEY=$(aws ssm get-parameter --name /conclave/judge-api-key --with-decryption \
>     --profile yeti-conclave --query Parameter.Value --output text)
> CONCLAVE_QUERYSET=hard python3 orchestrator/selfmoa_judge.py --mode select      # vs frontier's 0.753
> CONCLAVE_QUERYSET=hard python3 orchestrator/selfmoa_judge.py --mode synthesize   # CAN IT BEAT 0.813?
> ```
> `selfmoa_judge.py` takes `JUDGE_*` independently of `GRADER_*`, scopes its output files by
> judge model (so a Gemma run cannot overwrite the frontier run), and **REFUSES to report a
> result** if the judge ignores the candidates on >50% of queries or if judge == grader.
>
> ### ⚠️ LANDMINE (already handled in code, but you MUST honour the fairness rule)
> **Gemma-2's context is 8192 tokens and the 8 samples do not always fit.** Measured on the
> frozen samples: the candidate block is a median of ~4,600 tokens but a **max of 7,695** — so
> 6/30 queries overflow once you add the prompt and leave room for the judge to *write*.
>
> `selfmoa_judge.py` now truncates to `MAX_CANDIDATE_TOKENS` (**default 6000** — the largest
> budget that still fits: 6000 + ~300 prompt + ~1200 output = 7,500 < 8,192) and **reports how
> many queries were truncated**. Silent truncation is the dangerous version: it starves the
> judge, the judge scores badly, and you wrongly conclude *"synthesis doesn't work"* when the
> judge simply never saw the material.
>
> **🔴 THE FAIRNESS RULE — do not skip this.** The frontier numbers on record (**select
> 0.753**) were taken with **NO truncation**. A truncated Gemma run is therefore **NOT directly
> comparable** to them — the truncated judge would lose for the wrong reason. **Re-run the
> frontier arm at the SAME `MAX_CANDIDATE_TOKENS`** (it is offline and costs ~30 API calls), or
> compare only within a single budget. Otherwise you are measuring truncation, not judgment.
>
> **Also: Gemma has NO system role** — fold everything into the user turn (`judge_once` already does).
>
> ### PRE-REGISTERED PREDICTIONS (write these down before looking — that is the whole discipline)
> - **Gemma-select < frontier-select (0.753)**, but **> baseline (0.696)**. A weaker selector
>   still captures *some* of a +0.118 gap.
> - **Gemma-synthesize is the genuine unknown.** MoA's central claim is that *aggregation is
>   easier than generation* — a weak aggregator can still beat strong candidates. If true here,
>   Gemma-synthesize **exceeds 0.813** and the ceiling was never a ceiling. If it lands below
>   Gemma-select, then synthesis by a weaker model **destroys** information rather than combining
>   it, and selection is the only mechanism that works at this scale.
> - **If Gemma-synthesize beats 0.813, that is the headline of the whole project** — the
>   self-hosted small judge finally earns its keep, which is the v3 thesis, arriving by a route
>   nobody planned.
>
> Cost: ~$0.30 GPU (Gemma is 6GB — any cheap Ada card) + ~$0.50 grading.
>
> ### ➡️ THEN — the candidate-budget-matched experiment
> **Hold the candidate count at 8; vary only the SOURCE:**
> | arm | candidates | |
> |---|---|---|
> | **Self-MoA** | 8 samples from the best model | **incumbent: 0.753** |
> | **Mixed-MoA** | 8 samples across 3 comparable-strength, different-lineage models | challenger |
>
> The bar has MOVED: a new fleet no longer needs to beat "always call the coder" (0.696). It must
> beat **Self-MoA (0.753)** while carrying 3× the model footprint. Fleet research (27B/31B/30B,
> three orgs/architectures) is in the agent log; ~50% chance it is hierarchical too.
>
> **PRODUCTION SHAPE:** don't run 8 model copies. Use **`n=8` in ONE request** — vLLM processes
> the prompt once and forks 8 continuations sharing the prefill KV cache. 1× prefill + 8× decode,
> batched, latency ≈ a single request. **This guts the "3× inference cost" objection to the whole
> pattern.** (The sampler used 8 sequential calls — wasteful; `n=8` is the production shape.)
>
> ---
>
> # ✅ V3 (the FLEET question) IS ANSWERED. The verdict is SETTLED, on a SOUND instrument.
>
> **The ceiling was hiding NOTHING. The fleet is HIERARCHICAL, not complementary.
> ROUTE — do not judge.** (2026-07-14, n=30 pre-registered hard queries, H100.
> Everything below replays for **$0**: `CONCLAVE_QUERYSET=hard python3
> orchestrator/divergence.py`.)
>
> | | BASE (n=36) | **HARD (n=30)** |
> |---|---|---|
> | queries **at grader ceiling** | 31/36 (**86%**) | **6/30 (20%)** ← instrument FIXED |
> | best single (coder) | 0.933 | **0.696** |
> | oracle (a *perfect* judge) | 0.961 | 0.722 |
> | **HEADROOM** | **+0.0278** | **+0.0267** ← UNCHANGED |
> | 95% CI | [0.003, 0.052] | [0.004, 0.050] |
> | **verdict RESOLVED?** | **NO** | **NO** — was claimed YES, **RETRACTED** (z vs t; see the retraction block at the top) |
> | tied at top | 28/36 (78%) | 12/30 (40%) |
> | divergent | 24/36 (67%) | 24/30 (**80%**) |
>
> **The hard set did its job.** The ceiling collapsed 86% → 20%, scores fell
> 0.933 → 0.696, ties fell 78% → 40%, and the specialists now visibly disagree on
> **80%** of queries. **And the headroom did not move.** The old measurement was not
> wrong because it was saturated — it was *undecidable*. Now it is decided.
>
> ### Why disagreement TRIPLED but headroom stayed flat — **divergence ≠ headroom**
> The fleet is **hierarchical**: `coder 0.696 · general 0.527 · reasoning 0.518`.
> Strict wins **coder 12/30**, reasoning 4, general 2. And coder is now
> **SIGNIFICANTLY** the best single model — margin CI **[+0.084, +0.254]**, excludes
> zero (on the base set it did **not**: [−0.014, +0.136]).
>
> **The models disagree — but when they disagree, CODER IS USUALLY RIGHT.** So a
> *perfect* judge picking best-of-3 barely beats always calling coder. That is the exact
> signature of a fleet you **route to**, not one you **vote over**. `design.md` already
> called routing "the cheap sibling"; it is now the **measured recommendation**.
>
> ### The caveat, stated plainly
> The CI upper bound is **0.0498** against a 0.05 threshold — it clears "resolved" by
> **0.0002**. Razor-thin. Read it as *"not worth it, at the boundary"*, not a landslide.
> It cuts the right way though: both known biases (winner's curse on the oracle; in-sample
> pick of best-single) inflate headroom **in the ensemble's favour**, so the true value is
> if anything **lower**.
>
> ### What is NOT un-retracted
> **"The 14B coder is best on 31/36 queries" REMAINS FALSE** — a config-ordering artifact
> of a tie-blind `max()`. What is **newly established**, on a tie-aware counter and an
> unsaturated grader, is the weaker and real claim: **coder is significantly the strongest
> member.** The base set could not distinguish that. This one can.
>
> ### Pre-registration held
> The 30 queries were written and frozen **before any candidate was generated**, and **not
> one was re-worded after seeing a result.** That is what makes this null result credible.
>
> ## ➡️ WHAT TO DO NEXT (the v3 arc is complete)
> 1. **A ROUTER, not a judge.** The measured recommendation. Pick the right model per
>    request; do not fan out and vote. Cheap, and it is what the data supports.
> 2. **Or a DIFFERENT FLEET.** This one is one strong model plus two weaker ones — two of
>    the three are Qwen-lineage, which HANDOFF has flagged as denting decorrelation since
>    v2. A fleet with *comparable strength* and *genuinely different lineages* is the only
>    way the ensemble+judge pattern gets a fair test. **The AWS 8-vCPU quota chose this
>    fleet, not you** (it forced a 14B coder instead of 32B). That constraint is now gone
>    on RunPod. **Re-run `divergence.py` on any new fleet BEFORE building a judge for it** —
>    that is exactly what this instrument is for, and it costs one boot.
> 3. **Judge metrics (select mode, pairwise) are DOWNSTREAM of both.** Improving a judge
>    cannot recover value that is not there. Do not start here.

> ### ⛔ CORRECTION — the previous HANDOFF was WRONG about this step's cost.
> It said *"write HARDER QUERIES, then re-run divergence.py. **No GPU boot, no API key, ~$1.**"*
> **That is false.** New queries have **no cached candidates**, and candidates come only from the
> **self-hosted fleet**. There is no offline path: grading the hard set **REQUIRES A GPU BOOT.**
> Realistic cost: **~$3–5** (~45–75 min on a g6e + ~270 small grader calls). Budget accordingly.

### 🖥️ THE FLEET NOW RUNS ON RUNPOD (AWS g6e had no capacity). Read this before booting.

`runpod/` holds the whole path: `boot.sh` (kill-switch-first boot), `watchdog.sh`,
`fleet.json`, `fanout_direct.py`, `topup_coder.py`.

**Known-good pod config** (every field here was learned by it failing):
| field | value | why |
|---|---|---|
| GPU | **H100** (or L40S/L40/RTX-6000-Ada **IF driver ≥ 12.8**) | see driver trap below |
| image | RunPod **PyTorch** image — **NOT** `vllm/vllm-openai` | see entrypoint trap |
| start command | **EMPTY** | RunPod's start-command field overrides Docker **CMD, not ENTRYPOINT** |
| container disk | **80 GB** | weights are ~38 GB |
| network volume | **NONE** | network volumes are **datacenter-pinned** |
| ports | TCP **22** only, no HTTP | vLLM/LiteLLM bind to 127.0.0.1; reach them over an SSH tunnel |
| vLLM | `pip install --break-system-packages vllm==0.24.0` | the image is a Debian **PEP 668** env |

**Five RunPod traps, all hit live:**
1. **DRIVER.** vLLM 0.24's torch is **cu128**; it hard-fails on an older host driver
   (*"The NVIDIA driver on your system is too old (found version 12040)"*). **Three L40s
   in a row shipped driver 550 / CUDA 12.4.** The driver is a **HOST** property — **no
   image fixes it.** The H100 had driver 580 / CUDA 13.0 and worked. **Check
   `nvidia-smi` FIRST, before loading anything** — it costs 20 seconds and saves a boot.
2. **ENTRYPOINT.** `vllm/vllm-openai`'s entrypoint is `vllm serve`. RunPod's start
   command only overrides **CMD**, so your command is passed as *arguments to vllm serve*
   (it parsed `bash -c` as `--compilation-config`). The image then loads its own model and
   **eats 43 GB of the card**, and your fleet dies with *"Free memory ... less than desired
   GPU memory utilization"*. Killing that rogue `vllm serve` **kills the container** — it
   IS PID 1's child. **Use a PyTorch image instead.**
3. **PORT 8001 IS TAKEN** by RunPod's own proxy. `coder` died with *"Address already in
   use"* while 8002/8003 were fine; curl to 8001 returns RunPod's 502 HTML. **coder now
   runs on 8011.**
4. **A STOPPED POD LOSES ITS GPU.** RunPod has no reservation system — stop a pod and the
   card goes back to the pool. Ours was taken within minutes. **Do not stop-and-resume
   mid-workflow.** Either keep it running (the watchdog manages it) or terminate outright.
5. **ENV DOESN'T REACH SSH, AND IS FIXED AT CONTAINER START.** RunPod injects env into
   **PID 1 only** (`sshd` spawns a clean env — read `/proc/1/environ`), and *editing a
   secret does not reach a running pod*. So `boot.sh` prefers `/workspace/.runpod_key` and
   `/workspace/.hf_token`, which is how you fix a bad key **without a rebuild**.
   Push them from SSM: `aws ssm get-parameter ... | ssh pod 'cat > /workspace/.runpod_key'`.

**COST SAFETY — AWS's out-of-band kill switch does NOT survive this move.** CloudWatch
stopped a *wedged* box because the stopper lived **outside** it. RunPod has no equivalent;
everything runs on the pod. Three layers, weakest last:
1. **Prepaid credit balance** — outside the pod, absolute, survives total pod failure.
   **This is the real cap. Keep it small.**
2. **HARD TTL** (`watchdog.sh`) — stops the pod after `MAX_LIFETIME_MIN` no matter what.
   An idle rule **cannot** catch a crash-looping box (it is not idle); this can.
3. **Idle rule** — stops after `IDLE_MIN` of GPU <5%. **GATED on `/workspace/.fleet-ready`**
   because weight download is not GPU work: an ungated idle rule stops the pod
   **mid-download** and you pay to do it all again. That bug fired live.

`boot.sh` **FAILS CLOSED**: it proves the API key authenticates *and* can see this pod
**before loading a single model**. It caught a broken key twice. `podStop` is the
documented mutation (`podTerminate` is **not** in RunPod's docs — do not guess at an
undocumented call on a safety-critical path).

### ⛔ AWS capacity — escalation `esc-20260713-201140` (2026-07-13). Superseded by RunPod, kept for the fallback path.
Tried to boot and **could not**. g6e (the L40S 48GB box) is **exhausted in us-east-1**:
- `g6e.xlarge` **on-demand** — `InsufficientInstanceCapacity` in **all four** AZs (1c/1a/1d/1b).
- `g6e.2xlarge` **on-demand** (different pool, same single L40S, mem_utils unchanged) — **all four dry.**
- `g6e.xlarge` **spot** (a genuinely different pool) — dry too, then the documented silent stall.

**Smaller boxes are ruled out by physics, not preference:** quota is **G+VT = 8 vCPU**, and the
3-model fleet needs **~35 GiB**, so only the **48 GB L40S** fits. Every `g5.*`/`g6.*` is a 24 GB
A10G/L4 and **cannot hold the fleet**. Shrinking the fleet would change the very thing being
measured (fleet decorrelation *is* the experiment).

**What to do:** g6e capacity is **day-volatile** — dry in all AZs on 2026-07-09, then 1c launched
in ~20s the next morning. **Just retry on another day:**
```sh
aws sso login --profile yeti-conclave
python3 scripts/spend/authorize.py grant --usd 5 --ttl 2h --note "hard-set boot"   # RUN FROM THE REPO ROOT
scripts/sweep-gpu-capacity.sh g6e.xlarge us-east-1c us-east-1a us-east-1d us-east-1b
```
Then, **on the same boot** (a second boot doubles the GPU spend for the same information):
```sh
CONCLAVE_QUERYSET=hard CONCLAVE_GW=<ts-ip>:4000 python3 orchestrator/candidate_cache.py   # 30 x 3 = 90 calls
CONCLAVE_QUERYSET=hard CONCLAVE_GW=<ts-ip>:4000 python3 orchestrator/judge_eval.py --generate
cd infra && terraform apply -var enable_gpu=false     # TEAR DOWN before grading — grading is offline
CONCLAVE_QUERYSET=hard python3 orchestrator/divergence.py    # the headroom number
```

### 🐛 A LANDMINE FIXED THIS SESSION — `divergence.py` was CRASHING, so the "one next action" was impossible
`print_report()` read `best_candidate_counts`, a key the round-3 fix had renamed to
`strict_win_counts`. So `python3 orchestrator/divergence.py` died with `KeyError` **before**
`json.dump` — the report never saved. `--demo` never called the renderer, which is how three
review rounds missed it. Fixed; the demo now renders into a buffer so a missing key fails the
self-check. The committed `eval_divergence.json` **could not have been written by that code**;
regenerating it reproduces the numbers exactly (+0.0278, 31/36 at ceiling) and adds the round-3
fields the crash had prevented from ever being written.

### 🧪 THE HARD QUERY SET — `orchestrator/eval_queryset_hard.py` (n=30, 10/category)
The instrument that replaces the ceiling-limited base 36. **PRE-REGISTERED: written and frozen
BEFORE any candidate was generated.** Do **not** re-word a query after seeing which model wins —
that selects for disagreement and *manufactures* the headroom this exists to measure. If a query
is broken, **delete** it and say so.

Why the base 36 can't settle anything: **31 of them pin the best candidate at the grader's
maximum**, where headroom is 0 *by construction*. Their references are single-point facts
($0.05; 3 minutes), so a model that lands the point scores 5/5 and saturates. Every hard
reference instead enumerates **several independently checkable components**, giving the grader
resolution in the range these models actually occupy.

**Both outcomes are informative:** headroom still ~0 on *unsaturated* queries ⇒ the fleet is
genuinely **REDUNDANT** (route or cascade, don't judge). Headroom appears ⇒ the ceiling was
hiding real disagreement and the ensemble+judge question is live again.

Two gold references were **factually wrong** and were fixed before any candidate existed (both
would have marked *correct* answers down): `functools.lru_cache` is **not** built on
`OrderedDict`, and RFC 9110 defines **four** safe methods (TRACE was missing).

### ⚠️ Select the query set with `$CONCLAVE_QUERYSET=base|hard|all` (default `base`)
All candidate/judgment/report/divergence paths are now **scoped per set**. They were fixed at the
base filenames, so a hard-set `--generate` would have **overwritten the frozen published run**.
The base set keeps its historical filenames and still replays byte-for-byte for **$0**.
`judge_eval.py` and `divergence.py` both honour the selector; a query the fleet never answered is
now reported **loudly** instead of silently vanishing from `n`.

### 🐞 Known bug, not yet fixed: the spend guard's `AUTH_PATH` is relative to CWD
`scripts/spend/authorize.py` writes `.tessera/spend-auth.json` **relative to wherever you are**.
Grant from `~` and it lands in `~/.tessera/` and the guard (which also resolves relative to cwd)
never sees it. Fails *closed* there, which is safe — but it can also fail **OPEN**: run an agent
from a directory holding a stale grant and it is authorized by accident. **Resolve `AUTH_PATH`
against the repo root.** Always run the grant **from the repo root** until this is fixed.

**Verify the world still works in 10 seconds, for $0:**
```sh
python3 orchestrator/judge_eval.py --score      # replays the published run: 0.883 / 1.000, 0 live calls
python3 orchestrator/divergence.py --demo       # self-checks (all 5 modules have one)
```

## 🔴 READ FIRST — the fleet has no HEADROOM for a judge. Fix the fleet, not the judge.

Measured 2026-07-12 (`orchestrator/divergence.py`, frozen in `eval_divergence.json`,
reproduces for **$0**). Nobody had asked the prior question: **does this fleet give a judge
anything to arbitrate?**

**Fan-out + judge only pays when candidates are *comparably strong* and *genuinely
decorrelated*** — so different models win on different inputs. That is a property of the
**fleet**, testable *before* building any judge. The metric:

> ### HEADROOM = ORACLE (a perfect judge) − BEST SINGLE MODEL
> The entire value the ensemble+judge pattern can *ever* buy on a fleet.
> **Conclave's fleet: +0.028** (95% CI [+0.003, +0.052]).

| policy | score | cost |
|---|---|---|
| ORACLE — a *perfect* judge, best-of-3 | 0.961 | 3× inference + perfect judgment |
| **ALWAYS coder — one model, no judge** | **0.933** | **1× inference** |
| Gemma-judged ensemble (the v3 design) | 0.883 | 3× inference + judge + ~30% contention |

- Even a **perfect** judge buys **under 3 points** over one model. Every judge question
  (select mode, pairwise, a neutral grader key) is competing for that sliver.
- The real judge captures a **negative** fraction: **−0.050** vs always-coder
  (CI [−0.107, +0.007]).

**Why: the candidates are REDUNDANT, not hierarchical.** **28 of 36 queries are an exact TIE**
at the top — no model wins them. **Strict (unique) wins: coder 4 / general 4 / reasoning 0.**
There is simply very little disagreement to arbitrate.

> ### ❌ RETRACTED: "the 14B coder is the best candidate on 31/36 queries"
> A **config-ordering artifact**. `max()` returns the *first* maximum and
> `EnsembleConfig.candidates` is `[coder, reasoning, general]`, so all 28 tied queries were
> credited to coder. **Reverse the list and the same data says `general` wins 27.** The "one
> strong model carrying two weaker ones" story is **withdrawn** — `coder` is not even
> *significantly* better than `general` (margin CI [−0.014, +0.136] includes zero). Fixed in
> `divergence.py` (`strict_win_counts`; ties belong to nobody, counts are order-invariant).

### ⚠️ The measurement is CEILING-LIMITED — so the verdict is NOT settled
- **31/36 queries have the top candidate already at the grader's maximum**, where headroom is
  **0 by construction**. The entire +0.028 is earned on **5 queries**.
- **The verdict is NOT RESOLVED at n=36:** the CI is [+0.003, +0.052] and the "not worth it"
  threshold is 0.05 — the threshold sits *inside* the interval. "NOT WORTH IT" and "MARGINAL"
  are **not distinguishable** with this data. (The CI's *lower* bound is vacuous: headroom is
  ≥0 by construction, so "excludes zero" is guaranteed and is not evidence.)
- Both real biases (winner's curse on the oracle; in-sample pick of best-single) make the
  headroom **conservative**, not inflated — so the *direction* is trustworthy even if the
  verdict is not.

**This does NOT say ensembles don't work.** Multi-model systems work in production — but note
what they mostly do: **route** (pick the right model per request), not fan-out-and-vote.
`design.md` already called routing "the cheap sibling".

### Next actions, in order
1. **HARDER QUERIES FIRST — not a new fleet.** You cannot diagnose a fleet with an instrument
   pinned at its maximum. 31/36 queries are at the grader's ceiling, so the models have almost
   no room to *show* disagreement even if it exists. Write queries where the **best** candidate
   still loses points, then re-run `divergence.py` (free, offline). **Only this can tell you
   whether the fleet is genuinely redundant or the test is just too easy.**
2. **Then judge the fleet.** If headroom stays ~0 on hard, unsaturated queries, the fleet
   really is redundant → a different fleet (comparable strength, genuinely different
   strengths/lineages) or a **router** instead of a judge.
3. *Only then* judge metrics (select mode / pairwise, below). Downstream of both.

### The judge-vs-judge numbers (secondary now — and three claims were RETRACTED)

Kept because they are still the best judge-quality data we have, but they are **downstream**
of the finding above: improving a judge cannot recover value that isn't there.

**n=36, reference-graded:** Gemma **0.883** vs frontier **1.000**; paired gap **0.117, 95% CI
[0.045, 0.188], p ≈ 0.003**. Real *under this rubric* — but see the three retractions:

1. ❌ **RETRACTED: "the trap block discriminates 2.6× better."** **Not significant** (Fisher
   p = 0.146; Welch p ≈ 0.40). And the traps aren't traps — **every specialist answered every
   trap correctly**, so no fluent wrong answer was ever on the table.
2. ❌ **RETRACTED: "the grader self-bias caveat was FALSE."** Over-claimed. Correct: *no
   self-bias was **detected**, by an instrument with no resolution where it would show* — you
   cannot measure upward inflation on a variable pinned at the ceiling. The n=10 Gemini grades
   behind that claim are **not committed** and cannot be replayed.
3. ❌ **RETRACTED: "± 0.010 is the resolution floor."** That is grader *replication* noise (33
   of 36 items have stdev exactly 0). The statistic that bounds the claim is the **paired SEM
   = 0.0366**; the real floor is ≈0.07. `--score` now prints it — quote that, not the ±.

**Still standing:** the frontier judge sets `chosen == -1` on **34/36** — it ignores the
candidates and **writes its own answer**, so its 1.000 is largely "a frontier model answers an
easy question", not judging skill. **Gemma cannot win**; "0 wins" is arithmetic.

**Replay is real and free:** `python3 orchestrator/judge_eval.py --score` — **no env, no keys**
— reproduces 0.883/1.000 for **$0** from a fresh clone. (It previously crashed on a clean
checkout; nothing read `eval_fixtures/`. Defaults are now the frozen run's config, so the safe
path is the default and you must opt IN to spend.)

### If you still want a judge metric (AFTER fixing the fleet) — `select` mode. No key, no boot.

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

### (Demoted — NOT a blocker) an OpenAI API key, for pairwise on a FUTURE fleet

**No longer the ask.** Pairwise was previously "the most valuable next step"; it isn't. On a
fleet with **+0.028 headroom**, a sharper judge metric just measures the wrong thing more
precisely. `PairwiseScorer` stays **built, tested, and unrun**. Escalation
`esc-20260713-025337` is resolved as *superseded*. Pick this back up **only after** a fleet
with real headroom exists — then the key below is genuinely useful (a third house, neutral to
both Anthropic and Google). Details kept for that day:

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
- **Chunk 3 — judge eval (the thesis payload). ✅ DONE, then CORRECTED.** The 2026-07-11 claim
  ("Gemma 0.89–0.91 vs frontier 1.00, tied 15/18 → a small self-hosted judge holds up") is
  **WITHDRAWN** — see the READ FIRST block at the top. n=36 gives Gemma 0.883 vs 1.000, and the
  **ensemble as a whole scores below a single model**, which is the finding that matters. Full
  correction in `docs/chunk3-judge-eval-results.md`. Harness + frozen run in
  `orchestrator/eval_fixtures/` (replays for $0).
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
