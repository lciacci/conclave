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
# far more than the bug it prevents. So that one is DETECTED, not suppressed: added_files below
# records every file aider pulled in, and anything past the expected 2 is visible in the summary.
MENTIONED="orchestrator/bench_local30_grade.py"
EXPECTED_ADDS=2

command -v aider >/dev/null || { echo "ERROR: aider not on PATH."; exit 1; }
# Validate the cap BEFORE any task runs. run_capped re-checks, but by then a results dir exists and
# the first task has been announced — a misconfigured cap should never get that far.
case "$TASK_TIMEOUT" in ''|*[!0-9]*) echo "ERROR: TASK_TIMEOUT must be a whole number of seconds, got '${TASK_TIMEOUT}'."; exit 1 ;; esac
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
case "$AIDER_MODEL" in
  ollama_chat/*|ollama/*) ;;
  *) python3 -c "import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if sys.argv[2] in d else 1)" \
       "$MODEL_METADATA" "$AIDER_MODEL" \
       || { echo "ERROR: '${AIDER_MODEL}' has no entry in ${MODEL_METADATA}."; \
            echo "       Add one, or aider runs context-blind with the warning suppressed."; exit 1; } ;;
esac
# Never write into a results set that already holds a run. Evidence is the deliverable here.
if [ -s "${RESULTS}/summary.txt" ] && [ "${FORCE:-0}" != "1" ]; then
  echo "ERROR: ${RESULTS}/summary.txt already holds a run. Choose a new RESULTS dir, or FORCE=1."
  exit 1
fi

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
  case "$secs" in ''|*[!0-9]*) echo "FATAL: TASK_TIMEOUT must be a whole number of seconds, got '${secs}'." >&2; exit 2 ;; esac
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
  #   edit_rejects  aider counted failed blocks: editblock_coder.py:84
  #                 "# N SEARCH/REPLACE blocks failed to match!"  (N is a real count)
  #                 With applied=yes this means the model RECOVERED on a later turn — note, not fail.
  #   llm_turns=0   aider never got a completion back => endpoint/API failure => row is VOID
  #   llm_turns>1   aider re-prompted (file-mention reflection, or an edit retry)
  #   adds>2        aider pulled in an unexpected file (e.g. design.md via the docstring mention)
  local applied edit_rejects llm_turns adds
  applied=no; grep -q "^Applied edit to " "${RESULTS}/${id}.log" && applied=yes
  edit_rejects=$(grep -cE "^# [0-9]+ SEARCH/REPLACE block(s)? failed to match!" "${RESULTS}/${id}.log" || true)
  llm_turns=$(grep -c "^Tokens:" "${RESULTS}/${id}.log" || true)
  # A HEURISTIC context-inflation flag, not an exact census. aider announces command-line files as
  # "Added <path> to the chat" but echoes a later mention-path file as a BARE PATH line, so both
  # forms are counted and the total can over-count (a path quoted in prose also matches). What
  # matters is the comparison: adds > EXPECTED_ADDS means files beyond the two intended reached the
  # context. Counting only the first form reported adds=2/2 on a run that had grown to 32k with four
  # files in chat — the undercount hid the inflation that made the row worthless.
  adds=$( { grep -cE "^Added .+ to the chat" "${RESULTS}/${id}.log" || true; \
            grep -cE "^[A-Za-z0-9_./-]+\.(py|md|json|ya?ml|sh|txt)$" "${RESULTS}/${id}.log" || true; } \
          | paste -sd+ - | bc )

  printf 'task=%s leg=%s model=%s base=%s seconds=%s exit=%s diff_lines=%s applied=%s edit_rejects=%s llm_turns=%s adds=%s/%s\n' \
    "$id" "$LEG" "$AIDER_MODEL" "${BASE_FULL:0:8}" "$((t1-t0))" "$rc" \
    "$(wc -l <"${RESULTS}/${id}.diff" | tr -d ' ')" "$applied" "$edit_rejects" "$llm_turns" \
    "$adds" "$EXPECTED_ADDS" \
    | tee -a "${RESULTS}/summary.txt"

  # A dead endpoint mid-run yields nine clean-looking null rows. Fail fast instead: re-probe, and
  # stop the run if the endpoint is gone. Cheap (one HTTP call per task) next to a wasted pod-hour.
  if [ "$llm_turns" -eq 0 ]; then
    echo ">>> WARNING: ${id} recorded ZERO LLM turns — re-probing the endpoint."
    curl -sf --max-time 20 -H "Authorization: Bearer ${OPENAI_API_KEY:-x}" "$PROBE" >/dev/null \
      || { echo "FATAL: endpoint ${PROBE} is gone. ${RESULTS} is PARTIAL — rows after this point are missing, not passing."; exit 1; }
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
