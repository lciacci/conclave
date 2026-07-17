# Experiment — can the local Qwen 30B actually DRIVE Claude Code?

This is the thing the hard-30 benchmark could not test (it was single-turn QA). The
Phase-0 "daily-drive local" call is a **hypothesis**; this is the test that validates it.

## Run it

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

## The tasks (trivial → real — find the ceiling)

| # | Task to type into the Qwen session | What it tests |
|---|---|---|
| **T1** | "List the Python files in `orchestrator/` and summarize what `bench_local30_gen.py` does." | read + tool-use, no edits |
| **T2** | "In `orchestrator/bench_local30_gen.py`, add a one-line module-level comment at the top saying what LOCAL_CODER defaults to. Show me the diff." | single-file edit + verify |
| **T3** | "Add a `--dry-run` flag to `bench_local30_gen.py` that prints how many queries would be generated and exits without calling Ollama. Add a line to its `demo()` that exercises it." | multi-step edit + self-check |

Escalate only if the previous one worked. The point is to find where it stops being able to.

## What to record (rough notes per task)

- **Completed?** yes / partial / no
- **Tool-call errors** — did any tool call come back malformed / rejected by CC? how many?
- **Coherence** — stayed on task, or derailed / looped / forgot context?
- **Derail point** — if it failed, what was it doing when it broke?
- **Feel** — would you trust it for that class of task daily?

## Reading the result

- T1–T2 clean, T3 mostly works → local is a credible daily driver for real coding; the
  finding upgrades from "hypothesis" toward "supported."
- Breaks at T2/T3 (bad tool calls, derails, can't complete) → the local tier is too weak to
  drive CC; the real daily driver is the 80B / frontier, and local stays a cheap-lookup tier.
- Either way the **failure modes are the escalation signal** — they tell you which tasks to
  route up. Jot them down; they're the input to the (deferred) escalation policy.

Log the outcome back in `docs/HANDOFF.md` when done — it's the agentic evidence the
benchmark couldn't provide.
