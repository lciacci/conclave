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
| **T1** | "Summarize what `bench_local30_gen.py` does." | read + summarize, no edits |
| **T2** | "In `orchestrator/bench_local30_gen.py`, add a one-line module-level comment at the top saying what LOCAL_CODER defaults to. Show me the diff." | single-file edit + verify |
| **T3** | "Add a `--dry-run` flag to `bench_local30_gen.py` that prints how many queries would be generated and exits without calling Ollama. Add a line to its `demo()` that exercises it." | multi-step edit + self-check |

Escalate only if the previous one worked. The point is to find where it stops being able to.

## Grading rubric — FIXED 2026-07-28, before the hosted leg ran

Written down deliberately ahead of the numbers: a rubric settled after seeing the 80B's answers is
a rubric fitted to it. Applies identically to both legs.

- **Both legs run at the same PINNED `BASE`, never live HEAD.** Committing this harness between the
  legs would otherwise put the rubric below (T2's expected string, T3's spec) and
  `harness/results/*/T3.*.diff` (the literal T3 solution) into the second leg's worktree only.
- **The summary columns are FACTS, not verdicts. The table below is small and FAILS CLOSED.**
  Three review rounds killed the previous approach: each round added branches to close a gap and
  each set of branches opened a new one, and *every* gap biased the same way — toward flattering
  the model. Round 3's worst hole was that the single likeliest hosted-80B failure (a reply with
  prose or a whole-file block: `applied=no edit_reject_blocks=0 llm_turns=1 exit=0`) matched no branch
  at all. Enumerating the state space ahead of time did not work three times, so the table no
  longer tries to. Checked IN THIS ORDER — positive evidence of a model failure is checked FIRST,
  because a row can carry both a real failure and a harness artefact:
  1. **`edit_reject_blocks>0` and `applied=no` → MODEL failure, SCORED.** aider saw edit blocks and
     could match none of them; nothing landed. Checked before the timeout rule on purpose: a row
     that is both a real edit failure AND slow is still a real edit failure, and voiding it on the
     timeout would exclude the 80B's likeliest failure mode.
     (`edit_reject_blocks` sums the N in aider's `# N SEARCH/REPLACE blocks failed to match!` — it
     counts BLOCKS. The earlier `edit_rejects` counted how many times aider complained, so 3 bad
     blocks in one turn scored 1 while 1 bad block across 3 turns scored 3 — backwards.)
  2. **`llm_turns=0` → VOID.** aider never received a completion — dead endpoint, wrong model id,
     vLLM 400. Evidence of nothing. The run aborts immediately UNLESS our own cap ended the task
     (`exit=143/137`), in which case it VOIDs and continues — but **two consecutive zero-turn tasks
     abort regardless**, because a wedged endpoint that accepts connections and never completes
     looks exactly like a very slow model and would otherwise bill the whole run.
  3. **`exit=143`/`137` with `edit_reject_blocks=0` → VOID.** OUR cap killed it while it was still
     working. Report as "did not complete within Ns", never as a wrong answer.
  4. **READ-ONLY task (T1) with `exit=0`, `llm_turns>=1`, `edit_reject_blocks=0` → grade the LOG.**
     T1 is passed via `--read`, so it can NEVER set `applied=yes` and `diff_lines` is always 0 —
     without this branch every clean T1 row fell through to the default and was mandatorily VOID,
     including all three reps of the canonical local set. Pass = an accurate summary, no edit
     attempted.
  5. **`applied=yes`, no other flag → grade the DIFF, by running it.** See the per-task criteria
     below. `edit_reject_blocks>0` here means the model emitted a bad block and then landed
     *something* — do NOT assume it recovered: the columns cannot prove the retry fixed the same
     edit rather than a later one failing. Read the log.
  6. **ANYTHING ELSE → VOID.** This is the fail-closed default and the point of the rewrite. An
     unenumerated state can never be silently scored in either direction. If the table cannot name
     it, it does not count — it gets re-run once and, failing that, reported as unclassified.
  - **Context divergence is checked by READING, not by a column.** `adds` and `max_sent` were
    deleted in review round 4: across four rounds neither ever changed a verdict, while each
    produced defects — `adds` in its broad form counted aider's own error dump as added files, in
    its narrow form could not exceed the expected count at all, and `max_sent` implied token
    precision its rounded source does not have. A `.log` showing extra files entering the chat, or
    a `.diff` touching anything outside `bench_local30_gen.py`, still voids the row.

- **Three rules that keep the fail-closed default from becoming an escape hatch.** VOID is not a
  retry budget; without these it would quietly re-roll the dice until the model produced a
  scoreable answer, which is selection on the outcome and drifts the same favourable direction as
  every hole it replaces:
  1. **One re-run per task, maximum.** If the re-run also voids, stop.
  2. **Void counts are a RESULT and are always reported beside the grade.** "3 of 9 hosted rows
     were unclassifiable" is a finding about the model and the harness, not a footnote.
  3. **k-of-N uses non-void rows as the denominator, and the void count travels with it.**
     "2/2 with 1 void" is never to be written as "2/2".
- **Grade k-of-3. Repeats are NOT redundant, even at `temperature=0`.** This was gotten wrong twice
  on 2026-07-28; the evidence, in order:
  1. The k3 set's three reps were **byte-identical**, which looked like proof the stack is
     deterministic and repeats measure nothing.
  2. The v2 set, same config and same pinned BASE, produced `T1.r1`=22s, `T1.r2`=8s, and
     **`T1.r3` running into the 420s cap** (`exit=143`) after fabricating a query dataset and
     failing 3 SEARCH/REPLACE blocks against a READ-ONLY file.
  Aider does send `temperature=0` (`models.py` — `use_temperature` resolves to `0`), but temp-0 is
  not a reproducibility guarantee through Ollama's batching and KV cache. Identical reps are
  common, not reliable — and the rare divergent rep is the interesting one. **One run can miss the
  only failure in six.**
- **The run1→run2 T3 flip was still NOT sampling.** run1 used the pre-fix harness; adding one
  read-only file to the context turned a broken T3 into a correct one. That is context sensitivity,
  and it is why `BASE` and the added-file set are pinned. Both effects are real and separate.
- **T1 — pass = an accurate summary of `bench_local30_gen.py`; no edit attempted.** The old
  "list the Python files in `orchestrator/`" clause and its confabulation side-observation are
  GONE — see the note on `T1_MSG` in `run-t1t3.sh`. Measured, that clause did not probe
  confabulation; it drove aider's file-mention loop until the wall-clock cap
  (`results/t1t3-local30-t1loop/`). Confabulation is now measured where it is gradeable: T3's
  applied diff, by running it.
- **T2 — pass = a module-level comment stating `LOCAL_CODER` defaults to `qwen3-coder:30b`.**
  Placement inside the docstring (top vs bottom) is a **recorded nit, not a fail** — "at the top" is
  ambiguous for a file whose first statement is a docstring.
- **T3 — pass requires RUNNING the result, not reading the diff:** (a) `--demo` self-check still
  green, and (b) `OLLAMA_BASE=http://127.0.0.1:9 ... --dry-run` prints a count and exits 0 without
  dialing Ollama. Run1's T3 read plausibly and failed (b)'s sibling assert.

  > ### ⚠️ KNOWN GAP — (a) and (b) are NOT sufficient. Read the diff as well.
  > **Measured 2026-08-14:** `muse-glimmer:30b` passed both criteria on all three reps, and r1 had
  > silently dropped T3's second subtask. The prompt asks for a `--dry-run` flag **and** a line in
  > `demo()` exercising it; r1 shipped the flag, then wrote
  > `assert len([q for q in HARD_QUERY_SET if q["id"] not in c]) == 0` under a
  > `# exercise dry-run` comment — a tautology over an already-filled cache that never calls
  > dry-run. **A faked assertion exits 0 exactly like a real one**, so `--demo` stayed green and
  > this rubric scored the leg 3/3 PASS.
  >
  > **The structural reason, and it generalises past T3:** criterion (a) validates the model's work
  > using a self-check *the model itself edited*. That can never detect a dropped subtask inside
  > that self-check. `confabulated-completion` was already a named class here (2026-07-20, "fake
  > green") and the rubric written afterwards still cannot see it.
  >
  > **Until this is fixed, a T3 PASS means "the flag works", not "the task was completed."**
  >
  > The fix must be a post-condition the harness supplies and the model never sees. **It must not
  > match a literal output string** — the prompt never fixes one, and the two known-correct
  > solutions print different things (qwen: `Would generate 0 out of 30 queries`; muse r2/r3:
  > `dry-run: would generate 0/30 …`), so a string assertion would fail a solution this rubric
  > already grades PASS. A model-independent shape instead: **run `demo()` with the dry-run code
  > path instrumented from outside** — e.g. import the module, wrap the function the flag is
  > supposed to reach, run `demo()`, and assert the wrapper was called. That tests *"demo exercises
  > dry-run"* without assuming how either was written.
  >
  > Recorded, NOT fixed: it changes the pinned BASE tree, which would unmatch every leg run so far.

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
