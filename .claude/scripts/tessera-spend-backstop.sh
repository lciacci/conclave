#!/usr/bin/env bash
# Claude Code Stop hook: did a spend denial get dispositioned, or did it vanish?
#
# Spec 06's guard denies a command and then asks the model, in prose, to raise an escalation.
# That is model recall — the exact trigger this repo has watched fail twice (gate recorder,
# ~85% miss; doccheck's lesson, five more bugs). This makes the harness the trigger instead.
#
# A denial must end in a grant or a packet. Neither → exit 2, and the model must answer for it.
#
# Stdin: JSON with session_id, cwd, stop_hook_active.
# Exit 0 = quiet. Exit 2 = stderr fed back to the model, turn continues.
#
# Fails open on every error path — a backstop that can wedge a session gets ripped out.
set -u

# Spec 11: keep failing open, but say so. Binary lookup only — the event's destination
# comes from the hook JSON, so the ADR-0004 global tier ($HOME) is harmless here.
_HOOKDIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
degraded() {
  for _c in "$_HOOKDIR/../../bin/tessera-degraded" "$_HOOKDIR/../../scripts/tessera-degraded"; do
    if [ -x "$_c" ]; then "$_c" "$@" >/dev/null 2>&1 || true; return 0; fi
  done
  command -v tessera-degraded >/dev/null 2>&1 && tessera-degraded "$@" >/dev/null 2>&1
  return 0
}

# QUIET: no stdin means this was not driven by Claude Code.
HOOK_INPUT=$(cat 2>/dev/null || true)
[ -z "$HOOK_INPUT" ] && exit 0

# LOUD: without jq the backstop cannot check anything, so a denial that was never
# dispositioned stays invisible — the net silently stops existing.
if ! command -v jq >/dev/null 2>&1; then
  printf '%s' "$HOOK_INPUT" | degraded --component spend-backstop --reason jq-unavailable \
    --detail "jq is not on PATH; undispositioned spend denials go unchecked"
  exit 0
fi

# Already mid-continuation from a Stop hook: never re-fire into a loop.
ACTIVE=$(printf '%s' "$HOOK_INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null)
[ "$ACTIVE" = "true" ] && exit 0

SESSION_ID=$(printf '%s' "$HOOK_INPUT" | jq -r '.session_id // empty' 2>/dev/null)
CWD=$(printf '%s' "$HOOK_INPUT" | jq -r '.cwd // empty' 2>/dev/null)
# QUIET, and structurally unreportable — the log is keyed by session_id, so there is no
# file to write this complaint into. See docs/contracts/degraded-event.md.
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

# LOUD: no backstop means denials vanish undispositioned and the safety net never fires.
BACKSTOP="$PROJECT_DIR/scripts/spend/backstop.py"
if [ ! -f "$BACKSTOP" ]; then
  degraded --component spend-backstop --reason backstop-missing --session "$SESSION_ID" \
    --project "$PROJECT_DIR" --detail "$BACKSTOP absent; spend denials go undispositioned"
  exit 0
fi

# LOUD: backstop.py is stdlib-only, so any python3 works (the F-001/F-003 trap only bites
# hooks importing a third-party package) — but none at all means it cannot run.
if ! command -v python3 >/dev/null 2>&1; then
  degraded --component spend-backstop --reason no-python3 --session "$SESSION_ID" \
    --project "$PROJECT_DIR" --detail "python3 is not on PATH"
  exit 0
fi

# LOUD: wrong cwd — the 2026-07-24 class.
if ! cd "$PROJECT_DIR" 2>/dev/null; then
  degraded --component spend-backstop --reason cwd-unreachable --session "$SESSION_ID" \
    --project "$PROJECT_DIR" --detail "cannot cd to $PROJECT_DIR"
  exit 0
fi
python3 "$BACKSTOP" "$SESSION_ID" || exit 2
exit 0
