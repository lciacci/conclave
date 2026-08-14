# TOOL-DIRECTION — the design run that hasn't happened yet

**Status: options, not a decision.** Written 2026-08-10 to hold the reasoning from the session that
produced it, so a design run can start from here instead of re-deriving it. Nothing below is
committed to.

**The question:** the research phase is finished and its findings are settled. What makes conclave a
thing that gets *used* — daily, on real work — rather than a lab that produced a result?

---

## What already exists (checked, not recalled)

The "wire the local coder into a harness" step is **done and shipped**, and was still listed as a
next step when this was written:

- `harness/run-local-cc.sh` — launches Claude Code driven by the local Qwen through a LiteLLM
  Anthropic↔Ollama proxy. Starts the proxy if needed, backgrounds it, `--stop` kills it.
- `harness/litellm_config.yaml` — `qwen-local` (qwen3-coder:30b) as the brain, `qwen-fast`
  (qwen3:8b) for Claude Code's small/fast tier, with the `thinking`-param stripping that Ollama
  otherwise 400s on.
- `harness/run-t1t3.sh` — the matched-harness comparison instrument, five review rounds deep.
- `runpod/boot.sh` + `runpod/fleet_specialist.json` — the hosted tier, on demand.

So the gap is **not build effort**. It is that no workload has been identified where the local tier
is the right call, and that invoking it means launching an alternate Claude Code session rather than
calling it from the one you are already in.

## The lane constraint — read before proposing anything with "routing" in it

`docs/INTEGRATION.md` guard 3: **serving tiers ≠ routing policy. Conclave exposes tiers; Tessera
decides when to use them.** A router built here breaks the anti-conflation guard. What conclave can
own is *exposing* the tiers and *emitting the escalation signal*; the policy that consumes it is
Tessera's. This is not a formality — the guard exists because three projects kept re-deriving each
other's conclusions.

## The measurement that should drive the choice, and had not been read this way

Two results on the same local model, from different sessions, that answer "what is it good for":

| task shape | local 30B (Q4, Ollama) | reference | source |
|---|---|---|---|
| **edit-and-apply** | byte-identical 3/3 reps, **zero** edit rejects | matches hosted FP8 80B | `harness/results/t1t3-*`, 2026-07-28 |
| **find-the-defect** — `muse-glimmer:30b` | **0.309** recall, 5/8 criticals, 15 FP | claude reviewer 0.509, 6/8, 30 FP | S2 re-run, 2026-08-14 |
| ~~**find-the-defect** — `qwen3-coder:30b` @4096~~ | ~~**0.073** recall, 0/8 criticals~~ | ~~claude reviewer 0.509~~ | **RETRACTED** — see `docs/LOCAL-CODER-FAILURES.md` |

> ### ⚠️ 2026-08-14 — THE PREMISE BELOW WAS BUILT ON THE RETRACTED ROW. Re-argue, don't patch.
> Everything from here down was written when the local tier's review recall was believed to be
> **0.073 with zero criticals** — i.e. "it can type, but it cannot think." At matched budget on a
> current model it is **0.309 with 5-of-8 criticals and half claude's false-positive rate.** The
> asymmetry that motivated Option 1's whole shape (delegate the *typing*, keep the *judgement*) is
> roughly a third as large as it was, and the local tier is no longer disqualified from the review
> shape it was designed to be kept away from. **Option 1 may still be right — but it now has to win
> on the instruction-precision argument alone, not on "the alternative is impossible."**

**Task shape, not model tier — still directionally true, with a third of the magnitude.** The local
tier's strongest demonstrated competence is still *mechanically applying a change someone else
specified*. But the claim that review is *the shape that breaks it* no longer holds: it runs review
at ~2/3 of claude's recall for $0. What survives of the original observation is that
`run-local-cc.sh` hands it the entire agentic loop, and the multi-step confabulation failure
(`harness/results/t1t3-local30-run1-confounded/`) was never about review recall — it was about
self-report, and that has not been re-measured on any successor.

**Bound, stated up front:** the edit-apply parity is **n=3 and a capability FLOOR**, not a ranking.
Both models passed all three tasks, so the set discriminates nothing above that floor. Every option
below is being designed on that thin a base, and the first serious use may find the ceiling
immediately.

**Second bound, added 2026-08-14 — it was tested the same day, and it fired.** Both rows above were
properties of **`qwen3-coder:30b`**, not of "the local tier". Re-running the probe on **Muse Glimmer
30B** (Meta, Apache 2.0, in the Ollama library) took find-the-defect from 0.073 to **0.309**. The
prediction written here — *"if a successor scores materially above 0.073, Option 1 is designed
around a weakness the tier no longer has"* — is the case that occurred. **KAT-Coder-V2.5-Dev**
(Kwai, Apache 2.0, 35B-A3B, 69.4% SWE-bench Verified) is a second untested candidate; it needs a
GGUF conversion, so it was not run.

**The transferable rule:** every capability claim in this document is one model's, and the model
tier churns faster than the document. `orchestrator/s2_model_axis.py` takes `$LOCAL_CODER` and
replays for $0 — **re-run it before an option is chosen on the strength of a number, not after.**

---

## Option 1 — MCP server exposing the local tier as a delegated-edit tool

`conclave.apply(file, instruction)` runs on the free local tier and **returns a diff — never
writes**. The calling model stays the reasoner and the verifier; the local model does the typing.

**For:**
- **In-lane.** Exposes a tier, does not choose one. Guard 3 is satisfied by construction.
- **Aimed at the one measured strength.** Edit-and-apply is where the local model matched an 80B.
- **Supervision becomes structural rather than a discipline.** Returning a diff means the
  "it lies about being done" failure mode cannot land unreviewed. The known weakness is designed
  around instead of warned about.
- **Callable from a normal session.** No alternate CC launch, no context switch — that is most of
  the difference between a tool and a demo.
- **Every miss is a labelled entry** for `docs/LOCAL-CODER-FAILURES.md`, which is the signal
  Option 2 needs and currently has no source for.
- $0 marginal cost per call.

**Against:**
- **The load-bearing risk: instruction-precision cost.** If specifying the change precisely enough
  for a weak model to apply it takes as long as making the edit, the tool is worthless no matter how
  well it works. This is unmeasured and is the thing to test *before* building.
- **Latency.** The hosted 80B was ~4× faster than local on T3 (10s vs ~40s). Fine for a background
  mechanical edit; annoying inside an interactive loop.
- Built on n=3.
- It is a server to maintain, in a repo whose stated deliverable was an instrument.

**Open questions for the design run:**
1. What is the unit of work — a single-file edit, a diff application, a mechanical refactor across N
   files? At what N does delegation start beating doing it directly?
2. Does the failure log get written automatically on a rejected diff, or by hand?
3. Does it expose the hosted tier too, or local only? Hosted means spend, which means the cost gate.

## Option 2 — emit the escalation signal, don't build the router

Conclave publishes what the local tier measurably fails at; Tessera owns the policy that routes on
it. This is the lab↔frontier cascade — per the working notes, the one routing shape that survives
the within-fleet null, since within-fleet routing died with the specialist-fleet result.

**For:**
- **Correct lane split**, and already the agreed architecture. `docs/design.md` recorded the
  cascade's first measured point.
- **Cheap.** It is a data format and an emitter, not a policy engine.
- It is the piece that makes the local tier safe to use *without* supervision on the classes where
  it is competent — which is the only path to unattended use.

**Against:**
- **Blocked on data.** There is no signal without usage. `docs/LOCAL-CODER-FAILURES.md` is close to
  empty and fills passively.
- **Risk of schematising facts that do not exist yet** — the same failure `docs/FINDINGS.md` F-002
  argues against for a coordination database.
- The consumer lives in another repo, so the value only lands if Tessera builds the policy half.

**Open questions:**
1. What *is* the signal, concretely — a per-task-shape competence table, or a failure taxonomy?
2. Does it need to be machine-readable at n<10, or is a doc the honest format until then?

## Option 4 — no new build; use what exists and log the failures

Use `harness/run-local-cc.sh` on real work, record what breaks.

**For:**
- Zero build. It already works.
- Generates exactly the data Options 1 and 2 need, with no design commitment.
- The cheapest possible test of "is the local tier useful at all", which is genuinely open.

**Against:**
- **It tests the wrong shape.** `run-local-cc.sh` hands the local model the whole agentic loop —
  the shape it measurably fails at. A negative result here says little about Option 1's premise.
- **It has been the plan since 2026-07-28 and has produced almost no data.** Passive accumulation
  with no forcing function is a plan that has already been observed not working.

## Option 3 — ship the instrument (noted, deprioritised)

Package `divergence.py` / `fleet_pairwise.py` so someone outside can point it at a fleet and get
"don't ensemble" for $0, plus the write-up. It is the thesis deliverable and has never shipped.
Deprioritised because it is a **one-shot diagnostic and a portfolio artifact** — a different goal
from daily usage. Worth doing on its own terms if the demoable-platform story matters.

---

## Where the pros and cons actually point

The synthesis, offered as an argument rather than a conclusion:

**Option 4 as written tests the wrong shape, but a modified version of it de-risks Option 1 for
free.** Option 1's load-bearing risk is instruction-precision cost, and that is measurable without
building anything: take a fixed set of real edits already made, specify each as an instruction, run
it through the existing local endpoint, and compare the specifying time against the doing time.
Cheap, bounded, and it answers the one question that decides whether the MCP server is worth
existing. Then Option 1 if it clears; then Option 2 once there is a signal to emit.

That ordering is not decided. It is what a design run should argue with.
