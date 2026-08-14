# Conclave in the three-project system — stub

> **Canonical contract:** `../tessera/docs/contracts/three-project-cohesion.md` (Tessera-hosted,
> peer contract; hosting ≠ ownership). This file is a STUB — conclave's own lane + the shared
> anti-conflation guards. For the full map (layering, all seams, sequencing) read the canonical.
> If this stub and the canonical disagree, the canonical wins.

**Reconciled 2026-08-07** after pr-arbiter closed. The stale-banner version of this file (2026-07-28)
described a research-pattern-becomes-a-gate shape that no longer exists; it is replaced, not amended.

## What changed, and what it does to conclave

**pr-arbiter is frozen as a research artifact.** The pattern was extracted into **`arbiter`**
(`github.com/lciacci/arbiter`, `../arbiter`) — a shipping CLI that runs an adversarial review over a
git ref range: reviewer → independent second pass → two-voice KEEP/DROP/UNSURE triage → blocking /
advisory output, exit 1 on high or critical. It is **not a gate on anything**, and nothing gates it.

So conclave's measurements do not stop being useful; their **status** changes from *gate input* to
**design input**. The question is no longer "may this ship?" but "how should it be built?" — which is
the more useful thing to hand a tool author anyway.

**The headline: arbiter independently built what conclave measured.** One strong model
(claude-sonnet-4-6) plus role-differentiated prompts, no fleet. Conclave's model-axis result
(2026-07-28) is the confirming measurement for an architecture that was already chosen — and its
standing use is now forward-looking: *don't add a fleet later*.

## The three layers

| Layer | Project | Owns |
|---|---|---|
| **Substrate** | **Conclave** | Model serving — the tier ladder behind one OpenAI-compatible, Tailscale-private gateway. The measurement instrument (`orchestrator/divergence.py`, `orchestrator/fleet_pairwise.py`). |
| **Pattern** | **`arbiter`** (`../arbiter`) | The multi-ROLE, union-recall review engine + the typed-finding schema. Successor to pr-arbiter, which is frozen. |
| **Policy** | **Tessera** | Governance (gate / verify / watch / escalation) and the routing / escalation *decisions*. Hosts this contract. |

**Directionality (unchanged):** conclave is DOWNSTREAM of Tessera on governance (runs the framework:
`.tessera/` profile, gate-scan, suggestion-gate, escalation, findings feed up) and UPSTREAM as an
inference substrate. Governance flows down, inference flows up. Runtime peers. `arbiter` has the same
dual shape — it adopted `.tessera/` at scaffold, so it is a downstream too (canonical D4, resolved).

## Conclave's lane
- **Owns:** model serving (local coder daily-driver + hosted/frontier escalation tiers); the
  `divergence.py` / `fleet_pairwise.py` instrument.
- **Must NOT:** build routing policy (Tessera's) or the review pattern (arbiter's).

## Anti-conflation guards (mirrored here because they bind work IN this repo)

1. Conclave's "judge/ensemble doesn't pay" null is **select-best only** — do NOT cite it to block
   arbiter's **union-recall** review (different objective).
2. The diversity that pays is **ROLE**, NOT **MODEL**. One strong model + role-differentiated
   prompts; no fleet for the review pattern. **Now measured directly on the adversarial path — with
   a bound; see below.**
3. Serving tiers ≠ routing policy. Conclave exposes tiers; Tessera decides when to use them.
4. The review pattern's own numbers are thin. Gate on measurement, not on the headline. (The
   *original* form of this guard gated a graduation that no longer exists; what survives is the
   epistemic half.)

### Guard 2 — the 2026-07-20 open question, now partly answered

`orchestrator/s2_model_axis.py` measured the MODEL axis on the adversarial path, arms matched at two
passes (the candidate-count confound that retracted Self-MoA's number is exactly the trap here):

| arm | recall | matched/55 | crit | FP |
|---|---|---|---|---|
| best single — claude reviewer | 0.509 | 28 | 6/8 | 30 |
| **ROLE**-diverse union (claude rev ∪ claude arbiter) | **0.618** | 34 | 7/8 | 35 |
| **MODEL**-diverse union (claude rev ∪ muse-glimmer rev) | **0.527** | 29 | 6/8 | **61** |
| muse-glimmer:30b reviewer alone *(2026-08-14)* | 0.309 | 17 | 5/8 | 15 |
| qwen3-coder:30b reviewer alone, matched budget *(2026-08-14)* | 0.127 | 7 | 1/8 | 13 |
| ~~qwen3-coder:30b reviewer alone, 4096-token budget *(2026-07-28)*~~ | ~~0.073~~ | ~~4~~ | ~~0/8~~ | ~~16~~ |

MODEL diversity added **+1 matched finding (28 → 29) for +31 false positives (30 → 61)**. One match
in 55 is inside the draw spread arbiter measured on byte-identical input, so this is **not** a
decorrelated catch. Scorer reproduces pr-arbiter's committed numbers to 4dp using their matcher and
their expected findings.

### 🔴 2026-08-14 — the "~7× weaker" bound is RETRACTED, and guard 2 is STRONGER for it

Two corrections, from re-running the probe on `muse-glimmer:30b` (Meta, Apache 2.0, Aug 2026) with a
matched-budget qwen control. Both are $0 replays: `LOCAL_CODER=<model> CONCLAVE_MAX_TOKENS=16384
python3 orchestrator/s2_model_axis.py --gen`.

1. **`0.073` was partly a HARNESS artifact and must stop being quoted.** It ran at
   `CONCLAVE_MAX_TOKENS=4096`. At the 16384 the other arm needed, the *same* qwen build scores
   **0.127 (7/55, 1/8 criticals)**. The 3-finding difference is itself inside the draw spread, so do
   not claim "the budget caused it" either — the defensible statement is that **0.073 is not qwen's
   number at matched budget; 0.127 is**, and neither is precise. This is the repo's recurring
   failure mode #1 caught by its own control arm, which is the argument for running controls.
2. **The "weak model, near-subset, cannot add recall by construction" escape hatch is GONE — and
   the null survives without it.** Muse Glimmer is ~1.65× behind claude, not ~7×, and finds
   criticals on its own (5/8). It is a genuine second reviewer. Adding it still bought one match
   and doubled the false positives. **Guard 2 was previously supported by an argument; it is now
   supported by evidence.**

Still NOT settled: **peer-strength** (frontier-vs-frontier) diversity. A 1.65× gap is much closer to
peer than 7× was, but claude is still the stronger arm. Guard 2 continues to bind and is co-owned +
ADR-level; this block RECORDS the correction, it does not rewrite the canonical.
**⚠️ The retracted figure is quoted in `../arbiter` and `../tessera` — see `docs/FINDINGS.md` F-003.**

#### 2026-08-10 — arbiter's Round 3 was checked against that measurable. It does not move it.

`../arbiter/docs/STATE.md` § "Round 3" runs three arms on one diff (arbiter, a 17-agent
`/code-review`, cloud `ultra`) and reports union 7 / arbiter 2 / ultra 4 / workflow 7, with arbiter
and ultra each catching one the other missed. **All three arms are the same model.** Round 3 varies
*architecture*, so it cannot answer "does a second frontier MODEL decorrelate" — it is the Round 2
confound at line ~139 of the canonical contract, one rung better controlled (the 17-agent arm found
all seven, so scale does not explain arbiter's unique catch). Read it as *supporting* guard 2's ROLE
half — two arrangements of one model decorrelate — not as settling its MODEL question.

**Two corrections it does force on this table, and they are free:**

1. **These are single-draw point estimates.** arbiter re-ran one arm four times on byte-identical
   input and got **1–4 defects per run, union 5 of 7** — a 4× spread. The MODEL null no longer
   rests on the near-subset structure at all (see the 2026-08-14 block above — it now holds against
   a 1.65× model), but **0.618 vs 0.509 is still a gap with unmeasured spread.** Quote it as a
   direction, not a value. Anyone who needs it defended re-runs `orchestrator/s2_model_axis.py`;
   that is what the instrument is for.
2. **The control arm for any future model-diversity run is same-model-k-draws, not best-single.** A
   second draw from the *same* model took arbiter 2 → 5 of 7. A second *model* must beat that, or it
   is being credited for a re-draw effect. This is now a required arm in the pre-registration —
   without it the experiment cannot distinguish model diversity from sampling.

**Verdict on the queued paid experiment: still unspent.** The pre-registered trigger was "spend only
if the gap looks model-shaped." Round 3's gap is scale-shaped and triage-shaped.

## Running `arbiter` on conclave — what its reviews are, and are not

Recorded here 2026-08-07 because it had not crossed. arbiter has reviewed conclave code twice; the
*findings* landed (see `harness/run-t1t3.sh:139` and `:189`, both credited in place), but the
**usage rules that came with them did not**, and they are the part conclave needs before the next run.

- **The finder is better at LOCATING than at CONCLUDING.** Take the location, re-derive the
  consequence yourself. Measured across three foreign-repo runs: reported defects have been reliable,
  worked examples and proposed fixes have not. On conclave's watchdog it was right that the subshell
  leaks a `sleep` and wrong about what the leak does — it claimed the orphan goes on to `kill -KILL`
  the next aider invocation and fake an rc=137, which it cannot, since `kill "$wd"` takes the subshell
  down before it reaches its kill lines. Verified by running a reduced copy. Real defect: one leaked
  `sleep` per task, cosmetic.
- **A false premise does not always make a finding worthless.** One tessera finding rested on a
  premise that was false by construction and was still worth acting on, for a reason arbiter never
  stated. Judge the defect, not the argument.
- **`--ext ""` until the scope bug is fixed.** `is_reviewable()` filters on file extension, so
  extensionless shebang scripts are dropped and *the output does not say so* — it prints
  "N file(s) reviewed · 0 blocking" over files it never opened. `--path` cannot rescue it
  (`is_reviewable` runs first). Conclave's harness is shell; assume nothing is reviewed until the
  skipped-file list is printed. OPEN in `../arbiter` as its top item.
- **Cost:** conclave's one measured run was 1 shell file / 287 lines / **$0.79**, the most expensive
  per-file point in arbiter's table. Only *reviewable* files cost anything — a conclave branch
  changing 114 files had 113 logs and result data and one reviewable file. Scope with `--path`,
  and `--no-verify` roughly halves a run.

## Conclave's contributions to the seams

- The OpenAI-compatible Tailscale gateway that Tessera routing and arbiter's `base_url` consume.
  `arbiter` is deliberately not Claude-Code-bound — plain Python against the Anthropic SDK with a
  bare client, so `ANTHROPIC_BASE_URL` points it at conclave's gateway with no code change.
- The **S2 union-recall scoring variant** — **BUILT** as `orchestrator/s2_model_axis.py` (the model
  axis; the canonical's S2 row still reads "not built"). The generic *port* of the metric stays
  parked: a labeled corpus and a working recall harness already existed, so the real lever was more
  seeds, not the port.
- The escalation *tiers* (local → hosted → frontier); Tessera owns the *when*.
- **A result that bounds the gateway seam — read this before pointing arbiter at conclave.
  Substantially REVISED 2026-08-14; the old form said the local tier cannot review at all.**
  On structured adversarial REVIEW the local tier is *behind* claude, not absent from the task:
  **`muse-glimmer:30b` scores 0.309 recall / 5-of-8 criticals** against claude's 0.509 / 6-of-8 on
  the identical corpus and prompt — while *matching* the hosted 80B on edit-and-apply (T1–T3).
  The previously quoted 0.073 / 0-of-8 was `qwen3-coder:30b` at a starved token budget and is
  retracted above. **Task SHAPE still moves the needle more than model tier, but the review gap is
  ~1.65×, not ~7×** — which changes the seam from "cannot serve arbiter usefully" to "serves it at
  roughly two-thirds the recall, for $0." Whether that trade is worth taking is arbiter's call and
  Tessera's policy, not conclave's. Logged in `docs/LOCAL-CODER-FAILURES.md`.
- **Cohesion note:** conclave's "route, don't judge" IS Tessera principle #5 ("ensembling is a tool,
  not a default"), measured — conclave is the empirical arm of a stance Tessera holds as philosophy.

Full context in this repo: `docs/design.md` § "External validation + scope", `docs/HANDOFF.md`.
