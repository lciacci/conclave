# Conclave in the three-project system — stub

> **Canonical contract:** `../tessera/docs/contracts/three-project-cohesion.md` (Tessera-hosted,
> peer contract; hosting ≠ ownership). This file is a STUB — conclave's own lane + the shared
> anti-conflation guards. For the full map (layering, all seams, sequencing) read the canonical.
> If this stub and the canonical disagree, the canonical wins.

**Layer:** Conclave = the **substrate** (inference serving + the measurement instrument). The other
two: pr-arbiter = the **pattern** (the adversarial-quality review workflow — reviewer → independent
arbiter → mutual triage; "enhance testing via an adversarial process"), Tessera = the **policy**
(governance + routing decisions).

**Directionality:** conclave is DOWNSTREAM of Tessera on governance (runs the framework: `.tessera/`
profile, gate-scan, suggestion-gate, escalation, findings feed up) and UPSTREAM as an inference
substrate (Tessera routing/dispatch consumes conclave's gateway + tier ladder). Governance flows down,
inference flows up. Runtime peers.

**pr-arbiter shares the SAME DUAL SHAPE** (owner-set 2026-07-20): it is ALSO a downstream consumer of
the Tessera framework (will adopt `.tessera/`; canonical **D4** — direction set, timing parked) AND its
pattern graduates UP into Tessera's `/arbiter` (seam S4, ADR-gated). Framework flows down, contribution
flows up — mirror of conclave. **Adoption into `/arbiter` is VALUE-gated, not headline-gated** (guard 4).

## Conclave's lane
- **Owns:** model serving (local coder daily-driver + frontier escalation tiers); the
  `divergence.py` / `fleet_pairwise.py` instrument.
- **Must NOT:** build routing policy (Tessera's) or the review pattern (pr-arbiter's).

## Anti-conflation guards (mirrored here because they bind work IN this repo)
1. Conclave's "judge/ensemble doesn't pay" null is **select-best only** — do NOT cite it to block
   pr-arbiter's **union-recall** review (different objective).
2. The diversity that pays is **ROLE** (pr-arbiter), NOT **MODEL** (conclave's null). No fleet for
   the review pattern — one strong model + roles.
3. Serving tiers ≠ routing policy. Conclave exposes tiers; Tessera decides when to use them.
4. pr-arbiter's numbers are thin — gate any build on the instrument, not the headline.

> ⚠️ **Guard 2 is under an OPEN question (2026-07-20).** pr-arbiter predates the idea of MODEL-diversity
> in the adversarial path. Guard 2's evidence is conclave's **select-best** null; whether model-diversity
> adds **union-recall** (a *different* objective, per guard 1) on the adversarial path is **untested, not a
> settled no.** The guard **still binds** until an ADR changes it (co-owned + canonical). Recorded in
> `docs/HANDOFF.md` (top block) + `docs/S2-scoping.md` (addendum); gate on the instrument, not the instinct.

## Conclave's contributions to the seams
- The OpenAI-compatible Tailscale gateway that Tessera routing + pr-arbiter's `base_url` consume.
- The **S2 union-recall scoring variant** of `divergence.py` → Tessera's "is review-fan-out worth it?"
  gate, and the **value-gate on pr-arbiter's graduation** into `/arbiter`. **Currently PARKED** — a labeled
  corpus + working recall harness already exist in pr-arbiter, so the number exists and is thin; the real
  lever is more seeds (pr-arbiter's lane), not the port. Scoped in `docs/S2-scoping.md`; build only for a
  demo, or with MODEL as a variable (the one variant that adds new evidence).
- The escalation *tiers* (local → hosted → frontier); Tessera owns the *when*.
- **Cohesion note:** conclave's "route, don't judge" IS Tessera principle #5 ("ensembling is a tool, not a
  default"), measured — conclave is the empirical arm of a stance Tessera holds as philosophy.

Full context in this repo: `docs/design.md` § "External validation + scope", `docs/HANDOFF.md`.
