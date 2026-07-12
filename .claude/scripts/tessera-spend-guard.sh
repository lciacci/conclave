#!/usr/bin/env bash
# Claude Code PreToolUse hook (matcher: Bash): deny-by-default gate on external spend.
#
# Spec 06 / ADR-0005: an unsupervised agent in conclave is an agent that boots GPUs on
# its own. This blocks spend-COMMITTING commands with no live authorization. Cost-REDUCING
# commands (teardown, stop, enable_gpu=false) are never blocked — see guard.py's invariant.
#
# Stdin: JSON with tool_input.command. Exit 0 = allow. Exit 2 = block, stderr to the model.
#
# FAILS OPEN on infrastructure errors (no python, guard missing, wrong cwd) — a hook that
# wedges every Bash call is its own outage, and conclave's AWS layer (budget → SNS →
# hardstop lambda, plus idle-stop) bounds the damage out-of-band. guard.py itself fails
# CLOSED: if it runs at all, an unreadable or absent grant means no spend.
set -u

HOOK_INPUT=$(cat 2>/dev/null || true)
[ -z "$HOOK_INPUT" ] && exit 0

command -v jq >/dev/null 2>&1 || exit 0
CWD=$(printf '%s' "$HOOK_INPUT" | jq -r '.cwd // empty' 2>/dev/null)

PROJECT_DIR="${CWD:-$PWD}"
GUARD="$PROJECT_DIR/scripts/spend/guard.py"
[ -f "$GUARD" ] || exit 0

# guard.py is stdlib-only, so any python3 works (the F-001/F-003 bare-python3 trap only
# bites hooks that import a third-party package).
command -v python3 >/dev/null 2>&1 || exit 0

cd "$PROJECT_DIR" 2>/dev/null || exit 0
printf '%s' "$HOOK_INPUT" | python3 "$GUARD"
exit $?
