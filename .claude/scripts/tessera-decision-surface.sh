#!/usr/bin/env bash
case "$(dirname "$0")" in
  */.claude/scripts) cd "$(dirname "$0")/../.." 2>/dev/null || exit 0 ;;
esac
# Guarded: this script is ALSO installed to ~/.claude/templates/ as the ADR-0004 global
# fallback, where ../.. is $HOME, not a repo. In the global tier the session cwd IS the
# right signal — that copy has no repo of its own.

# PreToolUse(Edit|Write): print the ADRs and observatory entries that already govern the
# file about to be edited. Advisory — never blocks, always exit 0.
#
# WHY: 2026-07-24, an anchoring fix was built without accounting for ADR-0004's two-tier
# distribution and would have cd'd every downstream hook to $HOME. The ADR was in the repo,
# referenced from CLAUDE.md, and describes that exact failure mode. Which decision applies
# to the file you are touching rode pure model recall — principle #17 sitting on the design
# record itself. This makes it land mechanically, the way the handoff already does.

INPUT=$(cat 2>/dev/null || true)
[ -n "$INPUT" ] || exit 0

TARGET=$(printf '%s' "$INPUT" | python3 -c "
import json,sys
try: print((json.load(sys.stdin).get('tool_input') or {}).get('file_path') or '')
except Exception: print('')
" 2>/dev/null)
[ -n "$TARGET" ] || exit 0

# Once per file per session: a ten-edit sequence on one file should say this once.
SESSION=$(printf '%s' "$INPUT" | python3 -c "
import json,sys
try: print(json.load(sys.stdin).get('session_id') or 'nosession')
except Exception: print('nosession')
" 2>/dev/null)
SEEN=".tessera/logs/${SESSION}.surfaced"
mkdir -p .tessera/logs 2>/dev/null
if [ -f "$SEEN" ] && grep -qxF "$TARGET" "$SEEN" 2>/dev/null; then
    exit 0
fi
printf '%s\n' "$TARGET" >> "$SEEN" 2>/dev/null

# --hook makes decision_surface.py emit a PreToolUse `additionalContext` JSON envelope —
# the ONLY stdout that reaches the model on this event (bare text goes to the debug log,
# verified against the hooks docs). stderr is NOT folded into stdout: a crash must not
# corrupt the JSON the harness parses. On exit 0 the harness ignores stderr anyway.
# Stdlib-only by design, so bare python3 is correct here (CLAUDE.md's interpreter split).
OUT=$(python3 scripts/decision_surface.py --hook "$TARGET" 2>/dev/null)
[ -n "$OUT" ] && printf '%s\n' "$OUT"
exit 0
