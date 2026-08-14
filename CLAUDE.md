# CLAUDE.md — conclave

Project-specific guidance for Claude Code working in this repo.

## What this is

A self-hosted multi-model inference lab whose **research phase is finished**. Open-weight models
served via vLLM on RunPod (AWS is the documented fallback), reachable only over Tailscale; plus a
$0 4-bit Qwen3-Coder-30B running locally on the laptop via Ollama.

The original thesis was multi-model ensemble orchestration with a **judge**. It was measured and
**disproved on three fleets**, including a deliberately ideal peer-modern one and a genuine-specialist
one. What survives is **route, don't judge** — pick the right model per request, don't fan out and
vote — and the real deliverable is the **instrument** (`orchestrator/divergence.py`,
`fleet_pairwise.py`) that tells you whether a fleet is worth ensembling, for $0, before you build
anything. It correctly said "don't ensemble" all three times.

**The project has pivoted to practical use, local-first.** The open question is no longer *what is
true about ensembles* but *what makes this a tool that gets used*. Options are laid out in
`docs/TOOL-DIRECTION.md`; none is chosen.

Source of truth for design: `docs/design.md`. Latest state and what to resume: `docs/HANDOFF.md`.
Cross-project fit: `docs/INTEGRATION.md`.

## What is settled

Each of these is measured, and each cost real money or real time to learn. Don't re-derive them.

- **Route, don't judge.** Judge/ensemble does not pay on any of the three fleets tested. The
  specialist fleet is the most hierarchical of them: the 80B coder wins every category, including
  each specialist's own turf.
- **The diversity that pays is ROLE, not MODEL.** One strong model with role-differentiated prompts;
  no fleet for the review pattern. Measured on the adversarial path, not just inferred.
- **Peer-strength MODEL diversity is unmeasured, and the experiment to measure it is resolved as
  don't-spend.** See `docs/INTEGRATION.md` § Guard 2.
- **Task shape, not model tier — magnitude CORRECTED 2026-08-14.** The local 30B *matches* a hosted
  FP8 80B on edit-and-apply (byte-identical, zero edit rejects) and trails on find-the-defect —
  **`muse-glimmer:30b` 0.309 recall / 5-of-8 criticals vs claude's 0.509 / 6-of-8**, at *half* the
  false positives. The old "~7× (0.073 vs 0.509)" is **retracted**: it was `qwen3-coder:30b` at a
  starved 4096-token budget, and at matched budget that model scores 0.127. Direction holds, the
  ~7× does not. **Corollary that cost four months to learn: capability claims here are ONE MODEL's,
  and the local tier churns. Re-run `LOCAL_CODER=<model> python3 orchestrator/s2_model_axis.py
  --gen` ($0) before building on any of them.**
- **Local is the daily driver on COST, not on measured parity.** The Phase-0 coding-QA comparison
  was underpowered, and among the queries the grader could separate, the 80B won 10–2.
- **Recall figures in this repo are single-draw point estimates.** A 4× spread was measured on
  byte-identical input. Quote them as directions, not values; re-run the instrument if a number ever
  needs defending.

**Watch for the two recurring failure modes**, both of which have bitten this repo more than once:
reading a favourable non-result as a finding, and letting a retraction land only where the work
happened while the claim keeps circulating elsewhere.

## Positioning

Conclave is the **substrate** — serving tiers plus the measurement instrument — in a three-project
system with **Tessera** (governance and routing *policy*) and **arbiter** (a shipping review CLI).
The hard boundary: **conclave exposes tiers; Tessera decides when to use them.** Do not build routing
policy here. `docs/INTEGRATION.md` carries the guards that bind work in this repo; the canonical
contract is Tessera-hosted.

- **Tessera profile:** `standard` (see `.tessera/project.yml`).

## Working conventions

How the project owner works. The most important section.

- **Push back when you see drift.** Don't perform agreement. If a decision seems wrong or an
  assumption seems loaded, surface it — as honest feedback, not a refusal.
- **"Batching" is a one-word signal.** It means you're bundling decisions into prose instead of
  surfacing them as numbered choices. Stop, list the decisions, ask before committing.
- **Surface decisions before committing them.** Multi-step or irreversible changes warrant a
  brief "here's what I'd do, OK to proceed?" When you surface such a gate, **also record it**:
  `python3 scripts/gate/emit.py --fired --kind <kind> --note "<what you proposed>"` (use
  `--held` if you weighed surfacing one and decided against). This is Tessera principle #12 (the
  suggestion-gate) — the log is a reviewable journal of gate decisions. **A Stop hook now
  backstops this** (`scripts/gate/scan.py`): it counts gate-shaped turns in the transcript, diffs
  them against the log, and makes you adjudicate a gap before finishing — so forgetting to log a
  gate is now a bug, not just a finding. Its detector over-counts on purpose; you are the
  precision filter. Contract: the gate-event contract in the Tessera framework.
- **When you are blocked and cannot proceed, raise an escalation — do not just say so and stop.**
  `tessera-escalate raise --category <cat> --summary "<what is stuck>" --tried "<attempt — how it
  failed>" --option "<what to choose between>"` (if `tessera/bin` is not on your PATH, use
  `python3 scripts/tessera-escalate`). This is the suggestion-gate's *asynchronous* form: #12
  needs a human to dispose, and one is not always there. `--tried` is required — a packet with no
  attempts is a complaint, not an escalation. **This repo is the reason the channel exists:**
  three of the four organic escalations that justified it came from here (spot capacity
  exhausted, on-demand dry in all AZs, blocked on capacity) — each logged as a gate because
  there was nowhere else to put it. Resolve with `tessera-escalate resolve <id> --note "<the
  decision>"`. Contract: the escalation contract in the Tessera framework.
- **Use numbered lists for decision points.** Binary A/B beats a dense paragraph with embedded
  choices.
- **Name biases you notice in your own reasoning** — confirmation, sunk-cost, excitement,
  familiarity, anchoring. Honesty about bias is part of the trail.
- **Brief acknowledgments.** "Done," "Confirmed," "Clean" — not "Excellent! Great choice!"
- **Flag confidence levels.** Be explicit about what you know vs. infer vs. guess.
- **Tone is direct, not performative.** No witty-coworker framing.
- **Name the decision that flips before proposing a measurement.** If nothing changes with the
  result, don't propose it. Conclave is a toolset, not a case study — record the caveat, cite the
  command that would answer it, and move on. The instrument is the deliverable, not its output.

## Hook lifecycle (Mnemos)

The hooks in `.claude/settings.json` invoke scripts in `.claude/scripts/`:

- **SessionStart** — `mnemos-session-start.sh` loads any prior checkpoint
- **PreCompact** — `mnemos-pre-compact.sh` writes an emergency checkpoint before compaction
- **PreToolUse** — `mnemos-post-compact-inject.sh` checks for post-compaction restore;
  `mnemos-pre-edit.sh` (Edit/Write) checks fatigue + intent
- **PostToolUse** — `mnemos-post-tool.sh` logs tool outcomes
- **Stop** — `mnemos-stop-checkpoint.sh` checkpoints; `mnemos-stop-ingest.sh` ingests the
  transcript + scores haze

When you see `MNEMOS CHECKPOINT` in context, a hook injected it — announce briefly, resume from
it, don't re-derive. If no checkpoint fires on resume but `.mnemos/` exists, run `mnemos resume`.
The checkpoint records *activity*, not *pending decisions* — if it doesn't mention something you are
waiting on, that is a known gap, so still read `docs/HANDOFF.md`.

Requires the `mnemos` CLI on PATH (pip-installed globally). Hooks degrade gracefully without it.

## Don't

- Don't modify `.env` / `.env.*` (also denied in settings.json)
- Don't add dependencies without checking existing ones cover the need
- Don't commit secrets
- Don't build routing policy here — see Positioning
- Don't boot GPUs without surfacing a cost gate first. Every instance-hour is money. Wire the
  watchdog TTL before walking away, and **terminate** rather than stop — a RunPod stop wipes the
  container disk if there is no network volume
- Don't leave `docs/conclave-overview.html` stale. It is **manually deployed by upload**, so a commit
  does NOT update the live copy — the outward-facing page can be wrong while the repo is right. When a
  claim it carries is retracted or superseded, fix the page in the **same pass** and flag that it needs
  re-uploading. It once ran ~3 weeks publicly asserting a claim this repo had already retracted
- Don't create AWS resources without the project cost tag (see `docs/design.md` § cost controls)
- Don't expose public ports on an inference instance — Tailscale-only, no exceptions

## Commands

**The local tier (free, on-laptop):**
- `harness/run-local-cc.sh` — Claude Code driven by the local Qwen coder through a LiteLLM
  Anthropic↔Ollama proxy. `--stop` kills the background proxy. Drive it in prompt-mode: a weaker
  model can't run Bash/Edit unwatched.
- `harness/run-t1t3.sh` — the matched-harness local-vs-hosted comparison instrument.

**The instrument (offline, $0 against committed fixtures):**
- `python3 orchestrator/divergence.py` / `fleet_pairwise.py` — is this fleet worth ensembling?
- `python3 orchestrator/s2_model_axis.py` — union-recall on the adversarial path.

**The hosted tier (costs money — gate it):**
- `runpod/boot.sh` with `FLEET_JSON=runpod/fleet_specialist.json`. Supports per-card `device`
  pinning and per-model `extra_args`. The **RunPod MCP** (registered in `~/.claude.json`, key from
  SSM `/conclave/runpod-api-key`) drives pod create/list/terminate; its tools load only after a
  Claude Code restart. `nvidia-smi` before anything else — the driver is a per-machine lottery and
  vLLM 0.24 needs ≥580 / CUDA 13. Full SOP in `docs/design.md`.
- `aws ec2 describe-instances --filters "Name=tag:project,Values=conclave"` — the AWS fallback;
  capacity has been dry, RunPod is the live path.
- `tailscale status` — mesh reachability.
