#!/usr/bin/env python3
"""`restore_receipt` — the RECEIVER side of the restore receipt (T2, ADR-0015).

    python3 scripts/restore/emit.py --sufficient \\
        --used "goal + all 3 constraints; picked up the P14 work with no re-reading"

    python3 scripts/restore/emit.py --insufficient --missing progress \\
        --rederived "checkpoint had the goal but not what was already done; re-read active.md"

── WHY EVIDENCE IS REQUIRED AND A VERDICT IS NOT ENOUGH ───────────────────────────────

T2 asks the only question that matters about Mnemos: *after a discontinuity, did the
agent resume without re-deriving?* Only the receiver can answer it. But `gate/emit.py`
on pure model recall **missed ~85% of gates**, and a bare `--sufficient` flag would be
worse than that — it is one keystroke, and a reflexively-typed "sufficient" is exactly
as worthless as `restore_injected` was. That would rebuild the bug one level up.

So the receipt cannot be a verdict. It must NAME something: what was used, or what had
to be reconstructed. Specificity is the part that is hard to type without thinking.
The length floor is a speed bump, not a wall — it cannot stop a determined filler, and
is not trying to. It is there so the cheapest path is not the empty one.

**Emit at the END of a session, or the moment a gap is discovered — never at the start.**
At session start sufficiency is a PREDICTION: the checkpoint looks fine until turn 20,
when you find you do not know why something was decided. A first-turn receipt would
record optimism, not observation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

try:
    from . import paths
except ImportError:  # run as a loose script — no package context
    import paths

# Closed vocabulary, same reasoning as gate/emit.py's KINDS: 102 free-text gate events
# produced 33 unusable values. Fail-closed at emit is safe here — emit is model-
# interactive, so the model re-runs with a valid value in-turn.
MISSING = ("goal", "constraints", "progress", "files", "decisions")

MIN_EVIDENCE = 25  # chars; a speed bump against reflexive filling, not a wall


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_receipt(
    sufficient: bool,
    evidence: str,
    *,
    session_id: str,
    missing: str | None = None,
    ts: str | None = None,
) -> dict:
    """The contract-shaped event. Pairs with a `restore_offered` from the same session."""
    return {
        "type": "restore_receipt",
        "ts": ts or _utc_now_iso(),
        "session_id": session_id,
        "source": "model",
        "data": {
            "sufficient": sufficient,
            "missing": missing,
            "evidence": evidence,
        },
    }


def validate(args) -> str | None:
    """Returns an error string, or None. Evidence is mandatory in BOTH directions —
    'it worked' needs proof as much as 'it did not'."""
    if args.sufficient == args.insufficient:
        return "exactly one of --sufficient / --insufficient is required"
    evidence = (args.used if args.sufficient else args.rederived) or ""
    flag = "--used" if args.sufficient else "--rederived"
    if len(evidence.strip()) < MIN_EVIDENCE:
        return (f"{flag} must name something concrete (>= {MIN_EVIDENCE} chars). A bare "
                f"verdict is the failure mode this event exists to avoid.")
    if args.insufficient and args.missing not in MISSING:
        return f"--missing must be one of {', '.join(MISSING)}"
    if args.sufficient and args.missing:
        return "--missing is meaningless with --sufficient"
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Record whether a restored checkpoint sufficed.")
    p.add_argument("--sufficient", action="store_true")
    p.add_argument("--insufficient", action="store_true")
    p.add_argument("--missing", choices=MISSING, help="what the checkpoint lacked")
    p.add_argument("--used", help="what you actually used from the checkpoint")
    p.add_argument("--rederived", help="what you had to reconstruct yourself")
    p.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID", ""))
    args = p.parse_args(argv)

    err = validate(args)
    if err:
        print(f"restore/emit: {err}", file=sys.stderr)
        return 2
    if not args.session:
        print("restore/emit: no CLAUDE_CODE_SESSION_ID and no --session", file=sys.stderr)
        return 2

    evidence = (args.used if args.sufficient else args.rederived).strip()
    event = build_receipt(bool(args.sufficient), evidence,
                          session_id=args.session, missing=args.missing)
    path = paths.log_path(args.session)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(event) + "\n")
    print(f"restore receipt appended → {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
