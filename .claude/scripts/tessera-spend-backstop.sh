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

HOOK_INPUT=$(cat 2>/dev/null || true)
[ -z "$HOOK_INPUT" ] && exit 0

command -v jq >/dev/null 2>&1 || exit 0

# Already mid-continuation from a Stop hook: never re-fire into a loop.
ACTIVE=$(printf '%s' "$HOOK_INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null)
[ "$ACTIVE" = "true" ] && exit 0

SESSION_ID=$(printf '%s' "$HOOK_INPUT" | jq -r '.session_id // empty' 2>/dev/null)
CWD=$(printf '%s' "$HOOK_INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$SESSION_ID" ] && exit 0

PROJECT_DIR="${CWD:-$PWD}"
BACKSTOP="$PROJECT_DIR/scripts/spend/backstop.py"
[ -f "$BACKSTOP" ] || exit 0

# backstop.py is stdlib-only, so any python3 works (the F-001/F-003 bare-python3 trap only
# bites hooks that import a third-party package).
command -v python3 >/dev/null 2>&1 || exit 0

cd "$PROJECT_DIR" 2>/dev/null || exit 0
python3 "$BACKSTOP" "$SESSION_ID" || exit 2
exit 0
