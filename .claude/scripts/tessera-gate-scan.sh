#!/usr/bin/env bash

# Spec 11: report "I could not do my job" instead of exiting 0 into the dark. Resolved
# BEFORE the anchoring cd below, so $0-relative resolution cannot be broken by it.
# Inlined rather than sourced from a shared lib on purpose: a lib is one more file that
# can go missing on the very path that exists to report missing files (pattern #1).
_HOOKDIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
degraded() {
  for _c in "$_HOOKDIR/../../bin/tessera-degraded" "$_HOOKDIR/../../scripts/tessera-degraded"; do
    if [ -x "$_c" ]; then "$_c" "$@" >/dev/null 2>&1 || true; return 0; fi
  done
  command -v tessera-degraded >/dev/null 2>&1 && tessera-degraded "$@" >/dev/null 2>&1
  return 0
}
# ADR-0004 two-tier note: in the GLOBAL copy `$_HOOKDIR/../..` is $HOME, not a repo — but
# unlike the `cd` below that is harmless here. This resolves the BINARY only; the file the
# event is written to comes from the hook JSON's cwd/session_id, never from $_HOOKDIR.
# Worst case in the global tier is falling through to the PATH lookup.

# Anchor to the project root: hook commands inherit the SESSION cwd, which may be
# another repo entirely (2026-07-24 — a cd into a downstream split this repo's gate
# log 4/2 and silently no-op'd twelve hooks). $0 is a fact this script always has.
case "$(dirname "$0")" in
  */.claude/scripts) cd "$(dirname "$0")/../.." 2>/dev/null || exit 0 ;;
esac
# Guarded: this script is ALSO installed to ~/.claude/templates/ as the ADR-0004
# global fallback, where ../.. is $HOME, not a repo. Anchoring there would cd every
# downstream hook to $HOME and silently no-op it. In the global tier the session cwd
# IS the right signal — that copy has no repo of its own.
# Claude Code Stop hook: gate-scan backstop (principle #17).
#
# The surface-decisions *record* (scripts/gate/emit.py) rides model recall and
# misses ~85% of gates under real work. This makes the harness the trigger: count
# gate-shaped turns in the transcript, diff against the session's gate log, and
# exit 2 on a gap so the model must adjudicate before it can finish.
#
# Stdin: JSON with session_id, transcript_path, cwd, stop_hook_active.
# Exit 0 = quiet. Exit 2 = stderr fed back to the model, turn continues.
#
# Fails open on every error path — a backstop must never wedge a session.
set -u

# QUIET: no stdin means this was not driven by Claude Code (a hand-run, a smoke test).
# Nothing to do, and nothing to key an event on anyway.
HOOK_INPUT=$(cat 2>/dev/null || true)
[ -z "$HOOK_INPUT" ] && exit 0

# LOUD: every field this hook needs comes out of jq, so without it the scan cannot run at
# all. The gate corpus silently stops growing and that is indistinguishable from a session
# where no gate was missed. HOOK_INPUT goes on stdin because we cannot parse it ourselves
# here — tessera-degraded reads session_id/cwd with shell builtins for exactly this case.
if ! command -v jq >/dev/null 2>&1; then
  printf '%s' "$HOOK_INPUT" | degraded --component gate-scan --reason jq-unavailable \
    --detail "jq is not on PATH; gate-scan cannot parse its own hook input"
  exit 0
fi

# Already mid-continuation from a Stop hook: never re-fire into a loop.
ACTIVE=$(printf '%s' "$HOOK_INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null)
[ "$ACTIVE" = "true" ] && exit 0

SESSION_ID=$(printf '%s' "$HOOK_INPUT" | jq -r '.session_id // empty' 2>/dev/null)
TRANSCRIPT_PATH=$(printf '%s' "$HOOK_INPUT" | jq -r '.transcript_path // empty' 2>/dev/null)
CWD=$(printf '%s' "$HOOK_INPUT" | jq -r '.cwd // empty' 2>/dev/null)

# QUIET, and STRUCTURALLY UNREPORTABLE: the degraded log is keyed by session_id, so a hook
# with no session id has no file to write the complaint into. Named in the spec and in
# docs/contracts/degraded-event.md ("Known blind spot") rather than silently accepted.
[ -z "$SESSION_ID" ] && exit 0

# Anchor to the PROJECT ROOT, not the session cwd: `.cwd` is the SESSION's working
# directory and any Bash `cd` moves it. Live case 2026-07-26 — `cd scripts` made the spend
# guard resolve scripts/scripts/spend/guard.py -> absent -> spend commands ALLOWED. Same
# root cause silenced 12 hooks on 07-24 and made gate/ratio.py report ZERO over ZERO.
# CLAUDE_PROJECT_DIR is preferred but is EMPTY in some invocations, so relying on it alone
# is a no-op fix. Pure parameter expansion — no dirname/sed, these hooks must survive a
# broken toolchain, which is precisely when they have to report. See ADR-0015 / observatory.
# Sets $_root. Returns via a VARIABLE, not stdout: a PreToolUse hook's bare stdout is a
# real channel, and doccheck's pretooluse-hooks-reach-the-model rightly cannot tell a
# function's `printf` from the hook writing to it. Also saves a subshell fork per call.
_anchor_root() {
    _d="$1"; _root="$1"      # fall back to the input; never yield empty
    while [ -n "$_d" ] && [ "$_d" != "/" ]; do
        if [ -e "$_d/.git" ] || [ -f "$_d/.tessera/project.yml" ]; then _root="$_d"; return 0; fi
        case "$_d" in */*) _d="${_d%/*}" ;; *) break ;; esac
    done
}
_anchor_root "${CLAUDE_PROJECT_DIR:-${CWD:-$PWD}}"; PROJECT_DIR="$_root"

# LOUD: a Stop hook is always handed a transcript_path. Absent means the contract broke,
# not that there is nothing to scan.
if [ -z "$TRANSCRIPT_PATH" ]; then
  degraded --component gate-scan --reason no-transcript-path --session "$SESSION_ID" \
    --project "$PROJECT_DIR" --detail "Stop hook input carried no transcript_path"
  exit 0
fi

# LOUD: the scanner is the whole job. Missing → the Stop-hook backstop is a no-op, and an
# exit 0 here looks exactly like "no gates were missed".
SCAN="$PROJECT_DIR/scripts/gate/scan.py"
if [ ! -f "$SCAN" ]; then
  degraded --component gate-scan --reason scanner-missing --session "$SESSION_ID" \
    --project "$PROJECT_DIR" --detail "$SCAN"
  exit 0
fi

# LOUD: scan.py is stdlib-only, so any python3 works (the F-001/F-003 bare-python3 trap only
# bites hooks importing a package) — but no python3 at all means it cannot run.
if ! command -v python3 >/dev/null 2>&1; then
  degraded --component gate-scan --reason no-python3 --session "$SESSION_ID" \
    --project "$PROJECT_DIR" --detail "python3 is not on PATH"
  exit 0
fi

# LOUD: this is the 2026-07-24 bug — twelve hooks silently no-op'd on a wrong cwd.
if ! cd "$PROJECT_DIR" 2>/dev/null; then
  degraded --component gate-scan --reason cwd-unreachable --session "$SESSION_ID" \
    --project "$PROJECT_DIR" --detail "cannot cd to $PROJECT_DIR"
  exit 0
fi
python3 "$SCAN" "$TRANSCRIPT_PATH" "$SESSION_ID" || exit 2
exit 0
