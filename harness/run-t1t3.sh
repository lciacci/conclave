#!/usr/bin/env bash
# Headless T1-T3 runner — THE MATCHED HARNESS for both legs of the fidelity comparison.
#
# Why this exists: the 2026-07-22 hosted-80B run was CONFOUNDED because the two legs used
# different aider configs (local = aider-on-Ollama, whole-format edits that APPLIED; hosted =
# aider-on-raw-vLLM, whole-format edits that did NOT apply). Only the model may differ between
# legs. Everything below is fixed for both:
#
#   --edit-format diff   the fix for the swallowed edits (whole-format never landed the 80B's diff)
#   --map-tokens 0       the fix for the repo-map context blowup (74k -> vLLM 400)
#   BASE pinned          both legs check out the SAME tree (see BASE below)
#   portable timeout     the SAME wall-clock cap on macOS and Linux (see run_capped below)
#
# Each task runs from a CLEAN worktree at BASE, so tasks are independent and gradeable, and
# nothing touches the real checkout. Aider auto-commits inside the worktree: an empty T2/T3 diff
# therefore means "the edit was never APPLIED" — which is exactly the failure mode we're hunting.
#
#   harness/run-t1t3.sh                       # local leg (Ollama)
#   LEG=hosted80 AIDER_MODEL=openai/coder \
#     OPENAI_API_BASE=http://<pod>:8011/v1 OPENAI_API_KEY=x \
#     harness/run-t1t3.sh                     # hosted leg (raw vLLM)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "${HERE}/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"

LEG="${LEG:-local30}"
AIDER_MODEL="${AIDER_MODEL:-ollama_chat/qwen3-coder:30b}"
DEFAULT_MODEL="ollama_chat/qwen3-coder:30b"
MODEL_SETTINGS="${MODEL_SETTINGS:-${HERE}/aider.model.settings.yml}"
MODEL_METADATA="${MODEL_METADATA:-${HERE}/aider.model.metadata.json}"
TASK_TIMEOUT="${TASK_TIMEOUT:-420}"          # 7 min — the 80B is slow in-harness; 150s starved it
REPEATS="${REPEATS:-3}"
CONSECUTIVE_VOIDS=0    # see the zero-turn handling in run_task — two in a row stops a paid run
# Pinned, not derived. aider computes max_chat_history_tokens as
# min(max(max_input_tokens/16, 1024), 8192) (models.py), which yields 8192 for the local model
# (Ollama reports 262144) but 2048 for a 32768-token hosted endpoint. That silently gives the two
# legs different conversation-summarization behaviour on T3, the only multi-turn task — the hosted
# model would answer from a compressed transcript the local one never saw. Same number on both.
CHAT_HISTORY_TOKENS="${CHAT_HISTORY_TOKENS:-8192}"
WORKDIR="${WORKDIR:-/tmp/t1t3-${LEG}}"
RESULTS="${RESULTS:-${REPO}/harness/results/t1t3-${LEG}}"
TARGET="orchestrator/bench_local30_gen.py"

# BASE is PINNED, not read from live HEAD. If it floated, committing this harness between the two
# legs would put harness/EXPERIMENT.md (which states T2's expected string and T3's spec) and
# harness/results/*/T3.*.diff (the literal T3 solution) into the HOSTED leg's worktree only — the
# model could read the answer key, and the legs would no longer be the same task instance.
# a740565 is the last commit before this harness existed. Override only to move BOTH legs together.
BASE="${BASE:-a740565}"

# Pre-added read-only. NOT decoration: aider drops a reply's edits WITHOUT WARNING when that reply
# mentions a repo file not in the chat (base_coder.py — check_for_file_mentions() sets
# reflected_message and returns BEFORE apply_updates()). TARGET's own docstring ends "Then merge +
# grade with bench_local30_grade.py.", so any model quoting the docstring in a SEARCH block loses
# its own edit. That is what produced the 0-line T2 diff on 2026-07-28, and is the likely (not
# proven) mechanism behind the hosted 80B's swallowed T3 diff on 2026-07-22.
#
# TARGET's docstring ALSO says "see design.md", which matches tracked docs/design.md by basename and
# fires the same trap. design.md is 609 lines — pre-adding it would change the context both legs see
# far more than the bug it prevents. It is therefore NEITHER suppressed NOR auto-detected: the
# column that tried to detect it (`adds`) was deleted in review round 4 for never once changing a
# verdict while producing defects in both its forms. If a task's `.log` shows extra files entering
# the chat, or its `.diff` touches anything outside TARGET, the row is void — read the log.
MENTIONED="orchestrator/bench_local30_grade.py"

command -v aider >/dev/null || { echo "ERROR: aider not on PATH."; exit 1; }
# Validate the cap BEFORE any task runs. run_capped re-checks, but by then a results dir exists and
# the first task has been announced — a misconfigured cap should never get that far.
# 0 must be rejected, not just non-numerics: `sleep 0` returns instantly, the watchdog TERMs every
# task before aider gets a token, and all nine rows record exit=143 seconds=0 with no error.
# Arithmetic, not a glob: the `|0)` pattern rejected "0" but sailed past "00", "000" etc, each of
# which makes `sleep` return instantly and TERMs every task before aider gets a token.
[ "$TASK_TIMEOUT" -gt 0 ] 2>/dev/null || { echo "ERROR: TASK_TIMEOUT must be a positive whole number of seconds, got '${TASK_TIMEOUT}'."; exit 1; }
case "$REPEATS" in ''|*[!0-9]*|0) echo "ERROR: REPEATS must be a positive whole number, got '${REPEATS}'."; exit 1 ;; esac

# Guard the operator errors that produce PLAUSIBLE-LOOKING WRONG EVIDENCE. Both directions matter:
#   forward  — hosted model, LEG unset  -> hosted rows overwrite the local leg's results
#   backward — LEG=hosted80, model unset -> the LOCAL 30B is run and FILED AS HOSTED evidence,
#              passing the localhost preflight, while the rented H200 sits idle and billing.
if [ "$AIDER_MODEL" != "$DEFAULT_MODEL" ] && [ "$LEG" = "local30" ]; then
  echo "ERROR: AIDER_MODEL is '${AIDER_MODEL}' but LEG is still 'local30'."
  echo "       Set LEG explicitly, or this run overwrites ${RESULTS}."
  exit 1
fi
if [ "$LEG" != "local30" ] && [ "$AIDER_MODEL" = "$DEFAULT_MODEL" ]; then
  echo "ERROR: LEG is '${LEG}' but AIDER_MODEL is still the local default '${DEFAULT_MODEL}'."
  echo "       This would run the LOCAL model and file it as ${LEG} evidence."
  exit 1
fi
# The metadata fix is keyed on an EXACT model name. If it does not match, aider is context-blind
# and --no-show-model-warnings hides it — the 74k-context/vLLM-400 confound, silently reinstated.
# Applies to BOTH legs now. The old ollama_chat/* exemption was written when there deliberately was
# no local entry; now that both legs are pinned, exempting the local model means a renamed or
# typo'd key silently restores aider's trust in Ollama's advertised 262144 while num_ctx caps the
# real window at 32768 — the exact asymmetry the entry was added to remove, with no error.
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if sys.argv[2] in d else 1)" \
  "$MODEL_METADATA" "$AIDER_MODEL" \
  || { echo "ERROR: '${AIDER_MODEL}' has no entry in ${MODEL_METADATA}."; \
       echo "       Add one, or aider runs context-blind with the warning suppressed."; exit 1; }
# Never write into a results set that already holds a run. Evidence is the deliverable here.
if [ -s "${RESULTS}/summary.txt" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "ERROR: ${RESULTS}/summary.txt already holds a run. Choose a new RESULTS dir, or FORCE=1."
  exit 1
fi
# NOTE: the FORCE cleanup itself runs LATER, after every gate that can abort — see clear_previous().
# Deleting here would destroy the previous run's evidence and then exit on an unreachable endpoint
# or a bad BASE, leaving nothing in its place.
clear_previous() {
  [ "${FORCE:-0}" = "1" ] && [ -d "$RESULTS" ] || return 0
  echo ">>> FORCE=1 — clearing previous artefacts in ${RESULTS}"
  # Truncating summary.txt alone left the previous run's per-task .log/.diff in place: re-running
  # with REPEATS=1 after a REPEATS=3 run leaves r2/r3 artefacts with no row referencing them, and
  # these directories are graded by reading each .log against its .diff.
  find "$RESULTS" -maxdepth 1 -type f \( -name 'T*.log' -o -name 'T*.diff' -o -name 'summary.txt' \) -delete
}

# PREFLIGHT the inference endpoint. aider exits 0 on API errors, so without this a dead endpoint
# produces nine rows of 'exit=0 diff_lines=0 applied=no' that read exactly like a clean run.
case "$AIDER_MODEL" in
  ollama_chat/*|ollama/*) PROBE="${OLLAMA_API_BASE:-http://127.0.0.1:11434}/api/tags" ;;
  *)                      PROBE="${OPENAI_API_BASE:?set OPENAI_API_BASE for a non-Ollama model}/models" ;;
esac
curl -sf --max-time 20 -H "Authorization: Bearer ${OPENAI_API_KEY:-x}" "$PROBE" >/dev/null \
  || { echo "ERROR: inference endpoint not reachable: ${PROBE}"; exit 1; }

# ponytail: portable wall-clock cap — macOS ships no timeout(1) and the pod's Linux does, so using
# timeout(1) made the local leg run UNCAPPED while the hosted leg was capped at 420s. Same cap on
# both platforms is what matters; this is ~6 lines and depends on nothing.
run_capped() {
  local secs="$1"; shift
  # timeout(1) validated its duration and exited 125 before running the child; this replacement must
  # too. Without it a malformed TASK_TIMEOUT makes `sleep` fail instantly, the watchdog TERMs
  # immediately, and all nine tasks record exit=143 seconds=0 — indistinguishable, in the rubric,
  # from the model timing out on everything.
  # Same arithmetic check as the startup guard — kept in sync deliberately: this one is the backstop
  # for any future caller that reaches run_capped without passing through startup validation.
  [ "$secs" -gt 0 ] 2>/dev/null || { echo "FATAL: TASK_TIMEOUT must be a positive whole number of seconds, got '${secs}'." >&2; exit 2; }
  # KNOWN + DELIBERATE (arbiter, 2026-07-28): each task leaves ONE orphaned `sleep` behind. Killing
  # the watchdog subshell while it is blocked in `sleep` orphans that sleep, which then runs out its
  # full duration. It is harmless — a bare `sleep` holds no PID and runs no kill, so it cannot reap a
  # later aider and cannot fabricate an rc=137 void; the subshell dies before reaching its kill lines.
  # NOT fixed: the poll-loop alternative costs ~420 sleep forks per task to retire one idle process.
  "$@" & local pid=$!
  ( sleep "$secs"; kill -TERM "$pid" 2>/dev/null; sleep 10; kill -KILL "$pid" 2>/dev/null ) & local wd=$!
  local rc=0; wait "$pid" || rc=$?
  kill "$wd" 2>/dev/null || true
  return "$rc"
}

# T1 is read-only; T2/T3 edit.
#
# T1 ORIGINALLY read "List the Python files in orchestrator/ and summarize ...", kept deliberately
# as a confabulation probe: with --map-tokens 0 the model cannot see the directory, so an invented
# listing would be a fidelity failure. MEASURED 2026-07-28, that is NOT what it tests. Unable to see
# the directory, the model GUESSES filenames; every guess trips aider's file-mention path, which
# --yes-always auto-accepts; context climbs 5.6k -> 25k -> 32k against the 32768 ceiling and the
# task loops until the wall-clock cap. All three reps: 157s, 249s, 420s(SIGTERM), 3-4 LLM turns.
# The clause measured aider's file-mention loop, not the model. Dropped — T1 is now a clean
# read-and-summarize task, and confabulation is measured where it can be graded: T3's applied diff.
T1_MSG='Summarize what bench_local30_gen.py does.'
T2_MSG='In orchestrator/bench_local30_gen.py, add a one-line module-level comment at the top saying what LOCAL_CODER defaults to. Show me the diff.'
T3_MSG='Add a --dry-run flag to orchestrator/bench_local30_gen.py that prints how many queries would be generated and exits without calling Ollama. Add a line to its demo() that exercises it.'

mkdir -p "$RESULTS"
git -C "$REPO" rev-parse --verify --quiet "${BASE}^{commit}" >/dev/null \
  || { echo "ERROR: BASE '${BASE}' is not a commit in this repo."; exit 1; }
BASE_FULL="$(git -C "$REPO" rev-parse "$BASE")"
# Every abortable gate (endpoint preflight, LEG/model, metadata key, BASE) has now passed, so it is
# finally safe to destroy the previous run's evidence.
clear_previous
echo ">>> leg=${LEG} model=${AIDER_MODEL} base=${BASE_FULL:0:8} timeout=${TASK_TIMEOUT}s repeats=${REPEATS}"
echo ">>> results -> ${RESULTS}"

run_task() {
  local id="$1" msg="$2" mode="$3"   # mode: read | edit
  local wt="${WORKDIR}/${id}"
  git -C "$REPO" worktree remove --force "$wt" 2>/dev/null || true
  rm -rf "$wt"; mkdir -p "$(dirname "$wt")"
  git -C "$REPO" worktree add --detach --quiet "$wt" "$BASE_FULL" \
    || { echo "FATAL: worktree add failed for ${id} — run aborted, ${RESULTS} is PARTIAL."; exit 1; }

  local -a fileargs=(--read "$MENTIONED")
  if [ "$mode" = read ]; then fileargs+=(--read "$TARGET"); else fileargs+=("$TARGET"); fi

  echo ">>> ${id} running..."
  local t0 t1 rc=0
  t0=$(date +%s)
  run_capped "$TASK_TIMEOUT" env -C "$wt" aider \
      --model "$AIDER_MODEL" \
      --model-settings-file "$MODEL_SETTINGS" \
      --model-metadata-file "$MODEL_METADATA" \
      --edit-format diff \
      --map-tokens 0 \
      --max-chat-history-tokens "$CHAT_HISTORY_TOKENS" \
      --no-auto-commits \
      --no-stream --yes-always --no-check-update --no-analytics --no-show-model-warnings \
      --message "$msg" \
      "${fileargs[@]}" >"${RESULTS}/${id}.log" 2>&1 || rc=$?
  t1=$(date +%s)

  git -C "$wt" diff "$BASE_FULL" >"${RESULTS}/${id}.diff" || true

  # applied=no is AMBIGUOUS on its own — it fires for aider's silent file-mention drop (harness),
  # for a SEARCH block aider could not match (model), for our own timeout, and for a dead endpoint.
  # Recording only applied=no would let the 80B's edit-format failures be scored as our harness's
  # fault. Each discriminator below is ANCHORED on the exact string aider emits, not on a substring
  # that also appears in aider's system prompt — an unanchored "SEARCH/REPLACE" grep matched the
  # instructional text and flagged clean runs as model failures.
  #   edit_reject_blocks  aider counted failed blocks: editblock_coder.py
  #                       "# N SEARCH/REPLACE blocks failed to match!"  (N is a real count)
  #   llm_turns=0         aider never got a completion back => endpoint/API failure => VOID
  #   llm_turns>1         aider re-prompted (file-mention reflection, or an edit retry)
  #
  # DELETED after review round 4: `adds` and `max_sent`. Across four rounds neither ever changed a
  # verdict — every clean row read adds=2/2 and max_sent~5700 — while each produced defects: the
  # bare-path form of `adds` counted aider's own error dump as added files, its anchored form could
  # no longer exceed the expected count at all (making the rule it fed dead code), and `max_sent`
  # printed exact-looking totals from a source that rounds to the nearest 1000 above 10k. Three
  # columns' worth of maintenance for zero decisions. Context divergence between legs is instead
  # caught where it is unambiguous: the pinned BASE, the fixed --read set, and reading the log.
  local applied edit_reject_blocks llm_turns
  applied=no; grep -q "^Applied edit to " "${RESULTS}/${id}.log" && applied=yes
  # Count BLOCKS, not messages: `grep -c` counts how many TIMES aider complained, so 3 bad blocks in
  # one turn scored 1 while 1 bad block across 3 turns scored 3 — backwards. Match the FULL anchored
  # sentence (not the `^# N SEARCH/REPLACE block` prefix, which would also catch a numbered list or
  # a future message reformat), then sum the N.
  # `|| true` is LOAD-BEARING: `set -o pipefail` is on and grep exits 1 when it finds nothing, which
  # is the NORMAL case for a clean task. Without it the first successful task aborts the run.
  edit_reject_blocks=$( { grep -oE "^# [0-9]+ SEARCH/REPLACE blocks? failed to match!" \
    "${RESULTS}/${id}.log" || true; } | awk '{s+=$2} END{print s+0}')
  llm_turns=$(grep -c "^Tokens:" "${RESULTS}/${id}.log" || true)

  printf 'task=%s leg=%s model=%s base=%s seconds=%s exit=%s diff_lines=%s applied=%s edit_reject_blocks=%s llm_turns=%s\n' \
    "$id" "$LEG" "$AIDER_MODEL" "${BASE_FULL:0:8}" "$((t1-t0))" "$rc" \
    "$(wc -l <"${RESULTS}/${id}.diff" | tr -d ' ')" "$applied" "$edit_reject_blocks" "$llm_turns" \
    | tee -a "${RESULTS}/summary.txt"

  # ZERO LLM turns means aider never got a completion. Abort on the FACT, not on a re-probe: a vLLM
  # that 400s every chat request still answers GET /models with 200 forever, so a probe-gated abort
  # would sail through nine void rows and print a clean ">>> done" over a run the operator paid for.
  #
  # BUT exempt our OWN kill. If run_capped TERMed the task before aider's first completion, turns=0
  # is caused by the cap, not the endpoint — aborting there tears down a HEALTHY pod after one of
  # nine tasks and blames the server, and the rubric already treats a capped row as VOID-and-survive.
  if [ "$llm_turns" -eq 0 ] && [ "$rc" -ne 143 ] && [ "$rc" -ne 137 ]; then
    echo "FATAL: ${id} recorded ZERO LLM turns — aider never received a completion."
    echo "       Endpoint may be up but rejecting requests (context overflow, wrong model id)."
    echo "       ${RESULTS} is PARTIAL: rows after this point are MISSING, not passing."
    exit 1
  fi
  # The rc=143 exemption above is necessary (our own cap must not be blamed on the endpoint) but it
  # reopens the worst money hole: a pod that ACCEPTS the connection and never completes always ends
  # at the cap, so every task would VOID-and-continue and the run would bill a full hour, exit 0,
  # and print ">>> done" over nothing. One capped task is a slow model; two in a row is a wedged
  # endpoint. Stop paying at two.
  if [ "$llm_turns" -eq 0 ]; then
    CONSECUTIVE_VOIDS=$((CONSECUTIVE_VOIDS + 1))
    echo ">>> ${id}: no completion before the ${TASK_TIMEOUT}s cap fired (rc=${rc}) — VOID (${CONSECUTIVE_VOIDS} in a row)."
    if [ "$CONSECUTIVE_VOIDS" -ge 2 ]; then
      echo "FATAL: ${CONSECUTIVE_VOIDS} consecutive tasks produced ZERO LLM turns."
      echo "       The endpoint accepts connections but is not completing — a wedged or OOMed server"
      echo "       looks exactly like this. Stopping rather than billing the remaining tasks."
      echo "       ${RESULTS} is PARTIAL: rows after this point are MISSING, not passing."
      exit 1
    fi
  else
    CONSECUTIVE_VOIDS=0
  fi
  git -C "$REPO" worktree remove --force "$wt" 2>/dev/null || true
}

# Record the harness config IN the evidence. The header asserts "only the model may differ" between
# legs; without this, nothing in summary.txt can verify that — a 420s-capped local set and a
# 900s-capped hosted set produce rows of identical shape, and this script is editable between runs.
{
  printf '# harness=%s sha256=%s aider=%s\n' \
    "$(basename "$0")" \
    "$(shasum -a 256 "$0" 2>/dev/null | cut -c1-16 || echo unknown)" \
    "$(aider --version 2>/dev/null | tr -d '\n' || echo unknown)"
  printf '# leg=%s model=%s base=%s\n' "$LEG" "$AIDER_MODEL" "$BASE_FULL"
  printf '# task_timeout=%s repeats=%s edit_format=diff map_tokens=0 chat_history_tokens=%s auto_commits=off\n' \
    "$TASK_TIMEOUT" "$REPEATS" "$CHAT_HISTORY_TOKENS"
  printf '# endpoint=%s\n' "$PROBE"
} >"${RESULTS}/summary.txt"
for r in $(seq 1 "$REPEATS"); do
  run_task "T1.r${r}" "$T1_MSG" read
  run_task "T2.r${r}" "$T2_MSG" edit
  run_task "T3.r${r}" "$T3_MSG" edit
done
echo ">>> done. Grade k-of-${REPEATS}: read each .log (what it said) against its .diff (what landed),"
echo ">>> and for T3 RUN the result — the demo self-check + --dry-run against a dead endpoint."
