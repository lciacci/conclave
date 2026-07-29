#!/usr/bin/env python3
"""`restore_offered` — the HARNESS side of the restore receipt (T2, ADR-0015).

    python3 scripts/restore/offer.py            # called from mnemos-session-start.sh

── WHY THIS FILE EXISTS AT ALL ────────────────────────────────────────────────────────

`restore_injected` is a log line the hook wrote **about itself**. The log showed four;
the model received nothing on all four, because a PreToolUse hook's stdout went to the
debug log. Thirty-seven days of trial evidence was one party certifying its own delivery.

So T2 splits sender from receiver, and this is the sender:

    restore_offered   written by the HARNESS   "I delivered N bytes, these fields"
    restore_receipt   written by the MODEL     "what arrived, what I had to re-derive"
    restore/scan.py   diffs them at Stop       exit 2 on an unanswered offer

Neither party can certify itself. An offer with no receipt is a **detected** miss — the
one thing the old design could never see, because nothing independent recorded that a
restore was owed.

This records only what is mechanically true (bytes, fields present). It makes NO claim
about delivery — that is precisely the claim it is not entitled to make.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

try:
    from . import paths
except ImportError:  # run as a loose script — no package context
    import paths

# Mirrors bin/tessera-watch RESTORE_REQUIRED_FIELDS (P3/T1). Kept as a literal rather
# than imported: tessera-watch is a script, not a module, and this must never fail to
# record because an import moved.
CHECKPOINT_FIELDS = ("goal", "active_constraints", "task_narrative")


def build_offer(checkpoint: dict, size: int, *, session_id: str, ts: str | None = None) -> dict:
    """Mechanically-true facts only. No claim that any of it reached a model."""
    return {
        "type": "restore_offered",
        "ts": ts or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "session_id": session_id,
        "source": "mnemos-session-start",
        "data": {
            "bytes": size,
            "fields": [f for f in CHECKPOINT_FIELDS if checkpoint.get(f)],
            "goal_chars": len(checkpoint.get("goal") or ""),
        },
    }


def main(argv: list[str] | None = None) -> int:
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    if not session_id:
        return 0  # no key to file under; silent — this must never break session start

    ck = paths.root() / ".mnemos" / "checkpoint-latest.json"
    try:
        raw = ck.read_bytes()
        data = json.loads(raw)
    except (OSError, ValueError):
        return 0  # no checkpoint offered, so nothing is owed

    event = build_offer(data, len(raw), session_id=session_id)
    path = paths.log_path(session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(event) + "\n")
    except OSError:
        return 0  # recording an obligation must never become one
    return 0


if __name__ == "__main__":
    sys.exit(main())
