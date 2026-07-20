# Experiment — can the local Qwen 30B actually DRIVE a coding harness?

This is the thing the hard-30 benchmark could not test (it was single-turn QA). The
Phase-0 "daily-drive local" call is a **hypothesis**; this is the test that validates it.

**Two harnesses, same model, same tasks — the comparison is the point.** The CC run
(below) found the local coder *capable but SLOW*, and pinned the slowness on the harness:
Claude Code re-prefills a ~15k-token prompt every turn, so it's prefill-bound regardless of
model. Aider is the control: a *lighter* harness (repo map + only the files you `/add`, no
fat system prompt — the smoke test sent **1.4k tokens vs CC's ~15k**). Run the SAME T1–T3
on both and you separate "the model is slow" from "the CC harness is heavy."

## Run it — Claude Code harness

1. Make sure Ollama is running.
2. In a **fresh terminal** (leave your normal Claude session alone):
   ```
   cd /Users/lorenzociacci/Claude/conclave
   harness/run-local-cc.sh
   ```
   That starts the proxy and opens a Claude Code session whose brain is `qwen3-coder:30b`.
3. **Drive in prompt-mode** — approve each tool call as it appears. That's how you watch
   Qwen's choices, and it keeps a weaker model from running Bash/Edit unwatched.
4. When done: `Ctrl-C`, then `harness/run-local-cc.sh --stop` to kill the proxy.

## Run it — Aider harness (the lighter-harness control)

1. Make sure Ollama is running. No proxy — aider talks to Ollama directly.
2. In a **fresh terminal**:
   ```
   cd /Users/lorenzociacci/Claude/conclave
   harness/run-local-aider.sh
   ```
   Opens an aider session on `qwen3-coder:30b`. `num_ctx` is pinned to 32768 in
   `harness/aider.model.settings.yml` — Ollama's 2048 default silently truncates the prompt,
   which is the #1 aider+Ollama footgun (garbage output, no warning).
3. Aider workflow ≠ CC: `/add <file>` the files a task touches, type the request, review the
   diff it proposes. Auto-commit is ON, so **every edit is its own revertable commit** —
   `/undo` reverts the last one. That is the weak-model safety net (CC's is prompt-mode approval).
4. When done: `/quit`. Nothing is left running (no daemon, unlike the CC proxy).

**Keep the tasks identical across both harnesses** — T1–T3 below are model-and-harness
agnostic. Record wall-clock + fidelity for each so the two are directly comparable.

## The tasks (trivial → real — find the ceiling)

| # | Task to type into the Qwen session | What it tests |
|---|---|---|
| **T1** | "List the Python files in `orchestrator/` and summarize what `bench_local30_gen.py` does." | read + tool-use, no edits |
| **T2** | "In `orchestrator/bench_local30_gen.py`, add a one-line module-level comment at the top saying what LOCAL_CODER defaults to. Show me the diff." | single-file edit + verify |
| **T3** | "Add a `--dry-run` flag to `bench_local30_gen.py` that prints how many queries would be generated and exits without calling Ollama. Add a line to its `demo()` that exercises it." | multi-step edit + self-check |

Escalate only if the previous one worked. The point is to find where it stops being able to.

## What to record (rough notes per task)

- **Completed?** yes / partial / no
- **Tool-call errors** — did any tool call / edit come back malformed or rejected by the harness? how many?
- **Wall-clock** — time to complete (the CC↔aider comparison lives here; note cold vs warm model).
- **Coherence** — stayed on task, or derailed / looped / forgot context?
- **Derail point** — if it failed, what was it doing when it broke?
- **Feel** — would you trust it for that class of task daily?

## Reading the result

- T1–T2 clean, T3 mostly works → local is a credible daily driver for real coding; the
  finding upgrades from "hypothesis" toward "supported."
- Breaks at T2/T3 (bad tool calls, derails, can't complete) → the local tier is too weak to
  drive the harness; the real daily driver is the 80B / frontier, and local stays a cheap-lookup tier.
- **Cross-harness:** if aider is materially faster at the same fidelity, the CC "too slow"
  verdict is a *harness* limit, not a model limit — and the local coder's real home is a
  lighter harness, not CC.
- Either way the **failure modes are the escalation signal** — they tell you which tasks to
  route up. Jot them down; they're the input to the (deferred) escalation policy.

Log the outcome back in `docs/HANDOFF.md` when done — it's the agentic evidence the
benchmark couldn't provide.
