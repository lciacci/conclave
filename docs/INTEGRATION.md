# Conclave in the three-project system — stub

> **Canonical contract:** `../tessera/docs/contracts/three-project-cohesion.md` (Tessera-hosted,
> peer contract; hosting ≠ ownership). This file is a STUB — conclave's own lane + the shared
> anti-conflation guards. For the full map (layering, all seams, sequencing) read the canonical.
> If this stub and the canonical disagree, the canonical wins.

**Layer:** Conclave = the **substrate** (inference serving + the measurement instrument). The other
two: pr-arbiter = the **pattern** (multi-role union-recall review), Tessera = the **policy**
(governance + routing decisions).

**Directionality:** conclave is DOWNSTREAM of Tessera on governance, UPSTREAM as an inference
substrate. Runtime peers.

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

## Conclave's contributions to the seams
- The OpenAI-compatible Tailscale gateway that Tessera routing + pr-arbiter's `base_url` consume.
- A **union-recall scoring variant** of `divergence.py` → Tessera's "is review-fan-out worth it?"
  gate. (Cheap next lever; validates pr-arbiter before integration is built.)
- The escalation *tiers* (local → hosted → frontier); Tessera owns the *when*.

Full context in this repo: `docs/design.md` § "External validation + scope", `docs/HANDOFF.md`.
