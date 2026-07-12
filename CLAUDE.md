# CLAUDE.md — conclave

Project-specific guidance for Claude Code working in this repo.

## What this is

Conclave is a self-hosted multi-model inference lab on AWS. Open-weight models (70B-class)
served via vLLM behind a LiteLLM gateway, reachable only over Tailscale. The thesis: multi-model
ensemble orchestration with a **judge** — a meta-reasoner that selects/synthesizes across
parallel model responses. Purpose is learning cloud GPU infra, inference serving, and the
"meta-reasoners over specialized outputs" pattern family; plus a demoable platform story.

Source of truth: `docs/design.md`. Phases: v1 single model → v2 gateway + multi-model →
v3 ensemble + judge → v4 MCP front-end.

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
