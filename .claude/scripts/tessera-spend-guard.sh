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

# Spec 11: this hook fails OPEN by design (above), and an unguarded GPU boot is the one
# fail-open that already cost real money. Failing open is kept — but it must now SAY SO.
# Resolves the binary only; the event's destination comes from the hook JSON (ADR-0004:
# in the global tier `../..` is $HOME, which is harmless for a binary lookup).
_HOOKDIR=$(cd "$(dirname "$0")" 2>/dev/null && pwd)
degraded() {
  for _c in "$_HOOKDIR/../../bin/tessera-degraded" "$_HOOKDIR/../../scripts/tessera-degraded"; do
    if [ -x "$_c" ]; then "$_c" "$@" >/dev/null 2>&1 || true; return 0; fi
  done
  command -v tessera-degraded >/dev/null 2>&1 && tessera-degraded "$@" >/dev/null 2>&1
  return 0
}

# QUIET: no stdin means this was not driven by Claude Code. Nothing to guard.
HOOK_INPUT=$(cat 2>/dev/null || true)
[ -z "$HOOK_INPUT" ] && exit 0

# LOUD: without jq we cannot even find the project, so the guard cannot run and the
# command is ALLOWED. That is the failure mode this whole spec exists for.
if ! command -v jq >/dev/null 2>&1; then
  printf '%s' "$HOOK_INPUT" | degraded --component spend-guard --reason jq-unavailable \
    --detail "jq is not on PATH; the spend guard did not run and the command was ALLOWED"
  exit 0
fi
CWD=$(printf '%s' "$HOOK_INPUT" | jq -r '.cwd // empty' 2>/dev/null)
SESSION_ID=$(printf '%s' "$HOOK_INPUT" | jq -r '.session_id // empty' 2>/dev/null)

PROJECT_DIR="${CWD:-$PWD}"

# LOUD: a missing guard is indistinguishable, from outside, from "this command needed no
# guarding" — spec 11's words. rc=0 here ALLOWS the spend.
GUARD="$PROJECT_DIR/scripts/spend/guard.py"
if [ ! -f "$GUARD" ]; then
  degraded --component spend-guard --reason guard-missing --session "$SESSION_ID" \
    --project "$PROJECT_DIR" --detail "$GUARD absent; spend-committing commands are ALLOWED"
  exit 0
fi

# LOUD: guard.py is stdlib-only, so any python3 works (the F-001/F-003 bare-python3 trap
# only bites hooks importing a third-party package) — but none at all means no guard.
if ! command -v python3 >/dev/null 2>&1; then
  degraded --component spend-guard --reason no-python3 --session "$SESSION_ID" \
    --project "$PROJECT_DIR" --detail "python3 is not on PATH; the command was ALLOWED"
  exit 0
fi

# LOUD: wrong cwd — the 2026-07-24 class.
if ! cd "$PROJECT_DIR" 2>/dev/null; then
  degraded --component spend-guard --reason cwd-unreachable --session "$SESSION_ID" \
    --project "$PROJECT_DIR" --detail "cannot cd to $PROJECT_DIR; the command was ALLOWED"
  exit 0
fi

printf '%s' "$HOOK_INPUT" | python3 "$GUARD"
RC=$?
# LOUD: only rc=2 blocks and rc=0 allows deliberately. ANY other code means guard.py could
# not reach a verdict — a corrupt or half-written guard exits 1, and the spend proceeds.
# The exit code is preserved unchanged: reporting must not alter the allow/deny contract.
if [ "$RC" -ne 0 ] && [ "$RC" -ne 2 ]; then
  degraded --component spend-guard --reason guard-errored --session "$SESSION_ID" \
    --project "$PROJECT_DIR" --detail "guard.py exited $RC (not a verdict); the command was ALLOWED"
fi
exit $RC
