# CLAUDE.md — conclave

Project-specific guidance for Claude Code working in this repo.

## What this is

Conclave is a self-hosted multi-model inference lab. Open-weight models served via vLLM,
reachable only over Tailscale (fleet now runs on RunPod; AWS is the documented fallback).
The thesis STARTED as multi-model ensemble orchestration with a **judge** — a meta-reasoner
that selects/synthesizes across parallel responses. That thesis was **measured and disproved on
three fleets**: a judge does not pay on the old L40S fleet, on a deliberately ideal peer-modern one
(Qwen3-32B / Gemma-3-27B / Mistral-3.2-24B), or on a genuine-specialist one (Qwen3-Coder-Next-80B /
DeepSeek-R1-32B / Llama-3.3-70B) — the last is the MOST hierarchical: the 80B coder wins every
category and 100% of pairwise tie-breaks. The surviving finding is **route, don't judge** — pick the
right model per request; do not fan out and vote. The real deliverable is the **instrument**
(`divergence.py` / `fleet_pairwise.py`) that measures whether a fleet is worth ensembling, for
$0, before you build anything — it correctly said "don't ensemble" on all three fleets. Purpose
remains learning cloud GPU infra, inference serving, and the "meta-reasoners over specialized
outputs" pattern family; plus a demoable platform story.

Source of truth: `docs/design.md`; latest state: `docs/HANDOFF.md`. Arc: v1 single model → v2
gateway + multi-model → v3 ensemble + judge (**done, disproved**) → modern fleet (**done**) →
specialist-fleet Phase-1 gate (**done, RESOLVED — hierarchical, route to the coder**). Router is
**fleet-DEPENDENT and shelved**: it only pays when pairwise winners SPLIT (the peer fleet, weakly);
the specialist fleet CONCENTRATES, so no router. Judge is parked with a trigger (revisit if the
model landscape re-diverges).

**The thesis is DONE; the project PIVOTED to practical use (2026-07-17)** — stand up the owned coder
for real project + agentic work, **LOCAL-FIRST**. Phase-0: a $0 4-bit Qwen3-Coder-30B on the laptop
(Ollama, 64GB Mac) scored 0.900 vs the rented FP8 80B's 0.949 on coding-QA — UNDERPOWERED (n=30, CI
crosses 0), and among the queries the grader could separate the 80B actually won 10–2, so local is
the daily driver on **COST** (free, on-laptop), NOT on measured quality parity; the hosted 80B/H200
is the escalation tier. **Agentic competence is now MEASURED (CC wired to local Qwen via `harness/`,
2026-07-17):** it drives the tool-loop but is SLOW (prefill-bound on CC's ~15k prompts) and
LOW-FIDELITY (confabulated completing half a multi-step task) — a **SUPERVISED / background FALLBACK
tier, not an unsupervised peer** (auto-accept is unsafe: it lies about "done"). See `docs/HANDOFF.md`. Next practical step: wire the local coder into a harness (Claude Code
via a LiteLLM proxy). **Positioning:** Conclave is the **substrate** (serving + the `divergence.py`
instrument) in a three-project system with **Tessera** (governance + routing *policy*) and
**pr-arbiter** (the adversarial-quality review pattern). Both conclave and pr-arbiter are DOWNSTREAM
consumers of the Tessera framework AND contribute up (conclave serves + measures; pr-arbiter's pattern
graduates into `/arbiter`) — same dual shape. Conclave's null is SELECT-BEST only — it does NOT bind
pr-arbiter's union-recall, and the guard "diversity that pays is ROLE not MODEL" **still binds but is
under an OPEN question** (2026-07-20: model-diversity on the *adversarial* path is untested — a
different objective than the select-best null; see `docs/HANDOFF.md` top block + `docs/S2-scoping.md`).
See `docs/INTEGRATION.md` (conclave's stub; canonical cohesion contract is Tessera-hosted).

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

Requires the `mnemos` CLI on PATH (pip-installed globally). Hooks degrade gracefully without it.

## Don't

- Don't modify `.env` / `.env.*` (also denied in settings.json)
- Don't add dependencies without checking existing ones cover the need
- Don't commit secrets
- Don't launch, resize, or terminate AWS instances without surfacing a gate first — every
  instance-hour is money. Always confirm idle-stop is wired before walking away from a running box.
- Don't create AWS resources without the project cost tag (see `docs/design.md` § cost controls)
- Don't expose public ports on the inference instance — Tailscale-only, no exceptions

## Commands

No app build yet. Infra work:

- `aws ec2 describe-instances --filters "Name=tag:project,Values=conclave"` — what's running
- `tailscale status` — mesh reachability
- Phase scripts land in `scripts/` as they're built (start/stop instance, deploy vLLM config)
- **RunPod fleet:** `runpod/boot.sh` with `FLEET_JSON=runpod/fleet_specialist.json` (the modern
  specialist fleet: FP8 coder + R1 reasoner + Llama-70B general, one H100 each; `boot.sh` supports
  per-card `device` pinning + per-model `extra_args`). The **RunPod MCP** (registered in
  `~/.claude.json`, key from SSM `/conclave/runpod-api-key`) drives pod create/list/terminate — its
  tools load only after a Claude Code restart. Same cost gate as AWS: surface before you boot,
  wire the watchdog TTL before walking away, terminate (not stop) when done.
