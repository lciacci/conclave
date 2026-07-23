#!/bin/bash
# Tessera SQL lint gate — sqlfluff, WARN-ONLY, no-op when the repo has no SQL.
#
# ADR-0012 (supersedes ADR-0011). The shape is the one docs/observatory.md prescribed:
# on-demand, not an eager default (principle #15), and silent in projects with no SQL.
#
# ── WHY WARN-ONLY, AND WHY THE SHIPPED CONFIG EXCLUDES layout ────────────────────────────
# Measured on settempo 2026-07-21, the first downstream with standalone SQL: stock sqlfluff
# reports 206 violations, of which 185 (89%) are pure whitespace and the 21 survivors are
# 14 false positives (RF05 flags idiomatic Supabase RLS policy names; PG01 demands
# CONCURRENTLY on a fresh-install script where the tables are created empty in the same
# file and nothing can be locked) plus 7 nits (a column named `date`).
# A gate that blocks on that fails commits over spacing and wrong advice, and a gate that
# only ever cries wolf gets bypassed — then it protects nothing. So: warn, do not block,
# until a project's rules are tuned enough to earn blocking.
#
# ── THE ONE THING IT MUST NOT DO IS FAIL QUIETLY ─────────────────────────────────────────
# Spec 11's distinction, applied here:
#   "nothing to do"        → no staged .sql. Correct SILENT exit.
#   "could not do my job"  → sqlfluff unreachable. LOUD, every time.
# A linter that silently skips looks exactly like a linter that passed. That is the failure
# mode of F-001, the dead ingest pipe, and the falsifier — three in eight days.
#
# KNOWN LIMIT (same as doccheck's, inherited deliberately): sqlfluff reads the WORKING TREE,
# not the index. On a partial commit it judges content that is not exactly what is staged.
# Accepted until a partial commit actually bites.

set -uo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$ROOT" || exit 0

# Staged SQL only — an untouched .sql file elsewhere in the repo is not this commit's problem.
FILES=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null | grep -E '\.sql$' || true)
[ -z "$FILES" ] && exit 0        # nothing to do — the silent, correct exit

# Resolve a runner. Installed binary wins; uvx is the zero-install fallback (ADR-0012 chose
# uvx precisely so adopting this costs no dependency to maintain).
if command -v sqlfluff >/dev/null 2>&1; then
    RUNNER=(sqlfluff)
elif command -v uvx >/dev/null 2>&1; then
    RUNNER=(uvx sqlfluff)
else
    echo "" >&2
    echo "SQL-LINT DEGRADED: staged .sql files, but neither sqlfluff nor uvx is on PATH." >&2
    echo "  Files unchecked: $(echo "$FILES" | tr '\n' ' ')" >&2
    echo "  Install uv (https://docs.astral.sh/uv/) or 'pip install sqlfluff'." >&2
    echo "  Commit ALLOWED (warn-only) — but nothing checked this SQL." >&2
    echo "" >&2
    exit 0
fi

CONFIG_ARG=()
[ -f "$ROOT/.sqlfluff" ] && CONFIG_ARG=(--config "$ROOT/.sqlfluff")

# shellcheck disable=SC2086
OUTPUT=$("${RUNNER[@]}" lint "${CONFIG_ARG[@]}" $FILES 2>&1)
STATUS=$?

# sqlfluff: 0 = clean, 1 = violations found, anything else = it broke. The third case is the
# one that must not read as success — see the header.
if [ "$STATUS" -eq 0 ]; then
    exit 0
fi
if [ "$STATUS" -gt 1 ]; then
    echo "" >&2
    echo "SQL-LINT DEGRADED: sqlfluff exited $STATUS (it did not run to completion)." >&2
    echo "$OUTPUT" >&2
    echo "  Commit ALLOWED (warn-only) — but nothing checked this SQL." >&2
    echo "" >&2
    exit 0
fi

echo "" >&2
echo "SQL-LINT (warn-only — commit proceeds):" >&2
echo "$OUTPUT" >&2
echo "" >&2
echo "Not blocking. Tune .sqlfluff for this project, or fix the SQL." >&2
echo "" >&2
exit 0
