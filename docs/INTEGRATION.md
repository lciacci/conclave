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
| **MODEL**-diverse union (claude rev ∪ qwen rev) | **0.509** | 28 | 6/8 | **50** |
| qwen reviewer alone | 0.073 | 4 | 0/8 | 16 |

MODEL diversity added **+0.000 recall, +20 false positives, zero decorrelated catches**. Scorer
reproduces pr-arbiter's committed numbers to 4dp using their matcher and their expected findings.

**BOUND, and it is load-bearing: this added a much WEAKER model (~7×), not a peer.** A weak model's
findings are a near-subset, so it *cannot* add union-recall — the result is close to true by
construction. Directionally supportive of guard 2; it does **not** settle peer-strength diversity.
Guard 2 still binds (it is co-owned and ADR-level), and the open measurable is now sharper: **does a
second FRONTIER model decorrelate on union-recall?** See `docs/HANDOFF.md` § "What is actually left".

## Conclave's contributions to the seams

- The OpenAI-compatible Tailscale gateway that Tessera routing and arbiter's `base_url` consume.
  `arbiter` is deliberately not Claude-Code-bound — plain Python against the Anthropic SDK with a
  bare client, so `ANTHROPIC_BASE_URL` points it at conclave's gateway with no code change.
- The **S2 union-recall scoring variant** — **BUILT** as `orchestrator/s2_model_axis.py` (the model
  axis; the canonical's S2 row still reads "not built"). The generic *port* of the metric stays
  parked: a labeled corpus and a working recall harness already existed, so the real lever was more
  seeds, not the port.
- The escalation *tiers* (local → hosted → frontier); Tessera owns the *when*.
- **A negative result that bounds the gateway seam — read this before pointing arbiter at conclave.**
  On structured adversarial REVIEW, the local 30B scores **0.073 recall and 0/8 criticals** against
  claude's 0.509 on the identical task — while *matching* the hosted 80B on edit-and-apply (T1–T3).
  **Task SHAPE, not model tier, is the escalation trigger; review is the shape that breaks the local
  tier.** So the gateway can serve arbiter mechanically and cannot serve it usefully at the local
  tier. Logged in `docs/LOCAL-CODER-FAILURES.md`.
- **Cohesion note:** conclave's "route, don't judge" IS Tessera principle #5 ("ensembling is a tool,
  not a default"), measured — conclave is the empirical arm of a stance Tessera holds as philosophy.

Full context in this repo: `docs/design.md` § "External validation + scope", `docs/HANDOFF.md`.
