#!/usr/bin/env python3
"""Stop-hook backstop for the restore receipt (T2, ADR-0015).

    python3 scripts/restore/scan.py <transcript_path> <session_id>
    exit 1  -> the wrapper turns this into exit 2, forcing the model to answer

── WHAT THIS GUARANTEES, AND WHAT IT DELIBERATELY DOES NOT ────────────────────────────

It guarantees the TURN happens. It cannot guarantee the answer is honest — nothing can.
That is the same division of labour `gate/scan.py` proved: the harness makes the moment
occur, the model is the precision filter inside it. Pure model recall missed ~85% of
gates; a harness-created obligation is what closed that.

The obligation here is created by a party that cannot mark its own homework: the
SessionStart hook wrote `restore_offered`, so an offer with no `restore_receipt` is a
**detected** miss. The old design could never see this — nothing independent recorded
that a restore was owed, so a silent non-answer was indistinguishable from no restore.

── WHY IT ONLY FIRES ON SUBSTANTIVE SESSIONS ──────────────────────────────────────────

Every session restores (~121 and counting). An unconditional Stop-time demand would tax
all of them, and the failure mode is not noise — it is that `--sufficient` becomes a
reflex, which reproduces `restore_injected` one level up. So: only ask where the answer
could mean something. A session that edited nothing and ran a handful of turns never put
the checkpoint under load, and has no evidence to give.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    from . import paths
except ImportError:  # run as a loose script — no package context
    import paths

SUBSTANTIVE_TURNS = 20          # or...
SUBSTANTIVE_EDITS = 1           # ...any real file modification
EDIT_TOOLS = {"Edit", "Write", "NotebookEdit", "MultiEdit"}
MAX_FIRES_PER_SESSION = 2       # never nag unboundedly; a wedged hook gets ripped out


def read_events(session_id: str) -> list[dict]:
    path = paths.log_path(session_id)
    if not path.is_file():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def transcript_work(transcript_path: str) -> tuple[int, int]:
    """(assistant turns, edit-tool calls). Bad lines are skipped, never fatal."""
    turns = edits = 0
    try:
        f = open(transcript_path)
    except OSError:
        return 0, 0
    with f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") != "assistant":
                continue
            turns += 1
            for block in (d.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("name") in EDIT_TOOLS:
                    edits += 1
    return turns, edits


def is_substantive(turns: int, edits: int) -> bool:
    return edits >= SUBSTANTIVE_EDITS or turns >= SUBSTANTIVE_TURNS


def verdict(events: list[dict], turns: int, edits: int) -> str | None:
    """None = nothing owed. Otherwise the message to print before exiting nonzero."""
    kinds = [e.get("type") for e in events]
    if "restore_offered" not in kinds:
        return None                                  # no restore happened; nothing owed
    if "restore_receipt" in kinds:
        return None                                  # already answered
    if kinds.count("restore_scan_fired") >= MAX_FIRES_PER_SESSION:
        return None                                  # asked enough; do not wedge the session
    if not is_substantive(turns, edits):
        return None                                  # never put the checkpoint under load

    offer = next(e for e in events if e.get("type") == "restore_offered")
    d = offer.get("data") or {}
    return (
        "RESTORE RECEIPT OWED (T2, ADR-0015)\n"
        f"  A checkpoint was delivered to you this session: {d.get('bytes', '?')} bytes, "
        f"fields {d.get('fields') or []}.\n"
        f"  This session did real work ({turns} turns, {edits} edits), so it put that "
        "checkpoint under load — which makes it the only kind of session that can answer\n"
        "  the one question the Mnemos trial has never been able to: did you resume WITHOUT\n"
        "  re-deriving what you were doing?\n\n"
        "  Answer honestly; 'insufficient' is a finding, not a failure, and is the more\n"
        "  useful result. Do NOT reflexively report sufficient — that is the exact failure\n"
        "  (`restore_injected` said 'delivered' four times while the model got nothing).\n\n"
        "    python3 scripts/restore/emit.py --sufficient \\\n"
        "        --used \"<what you actually used from it>\"\n"
        "    python3 scripts/restore/emit.py --insufficient --missing "
        "<goal|constraints|progress|files|decisions> \\\n"
        "        --rederived \"<what you had to reconstruct yourself>\"\n"
    )


def record_fire(session_id: str) -> None:
    """Count the ask, so MAX_FIRES_PER_SESSION can be enforced next Stop."""
    path = paths.log_path(session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps({"type": "restore_scan_fired", "session_id": session_id}) + "\n")
    except OSError:
        pass


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        return 0
    transcript_path, session_id = argv[1], argv[2]
    if not session_id:
        return 0
    turns, edits = transcript_work(transcript_path)
    message = verdict(read_events(session_id), turns, edits)
    if message is None:
        return 0
    print(message, file=sys.stderr)
    record_fire(session_id)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
