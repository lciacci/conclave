#!/usr/bin/env python3
"""Stop-hook backstop: did a spend denial get dispositioned, or did it vanish?

Spec 06 shipped a guard whose deny path ends in a *prose instruction* — "raise a packet and
stop." That is model recall, and this repo has now watched model recall fail twice: the gate
recorder missed ~85% of gates, and doccheck's lesson sat in prose for five more bugs. **A
mechanism whose failure path rides recall has no failure path.**

It also invalidates the reason the escalation backstop was deferred. `docs/contracts/escalation.md`
argued escalation was less exposed than gate-recording *"because a blocked agent cannot proceed:
the failure mode is not silence but a summary that isn't a packet."* **Spec 06 made that false.**
The guard denies ONE tool call. The agent is free to do other work, take the offline path, or
simply move on — and the denial disappears with it. Under supervision you'd see it. Unsupervised,
nobody ever learns the envelope was wrong.

THE PREDICATE — a denial must end in one of two places, and this checks which:

    denied → a human granted an envelope     ✓ the supervised path, correct
    denied → an escalation packet was raised ✓ the unsupervised path, correct
    denied → neither                         ✗ the block vanished silently. FIRE.

Same posture as the gate-scan: a recall net the harness triggers, with the model as the
precision filter. But better-conditioned — `spend_denied` is a LOGGED EVENT, not a text
heuristic, so there is no over-counting to adjudicate away. If this fires, something really
was denied and really was never dispositioned.

    python3 scripts/spend/backstop.py <session-id>     # exit 2 if a denial vanished

Contract: docs/contracts/spend-authorization.md
"""
import json
import sys
from pathlib import Path

LOGS = Path(".tessera/logs")
ESCALATIONS = Path(".tessera/escalations")
MAX_FIRES = 3
FIRE_COUNT = Path(".tessera/.spend-backstop-fires")


def _events(session_id: str, root: Path | None = None) -> list[dict]:
    path = (root or LOGS) / f"{session_id}.jsonl"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a torn line must not hide the rest of the session
    return out


def _escalated(session_id: str, root: Path | None = None) -> bool:
    """Did this session raise any packet? Category is not required to be spend-shaped —
    an agent that escalated *something* while blocked has not silently routed around."""
    for path in (root or ESCALATIONS).glob("*.json"):
        try:
            if json.loads(path.read_text()).get("session_id") == session_id:
                return True
        except (OSError, json.JSONDecodeError):
            continue
    return False


def undispositioned(events: list[dict], escalated: bool) -> list[dict]:
    """The denials that ended nowhere. Empty list == nothing to answer for."""
    if escalated:
        return []
    denials = [e for e in events if e.get("type") == "spend_denied"]
    if not denials:
        return []
    last_denial = max(e.get("ts", "") for e in denials)
    # A grant AFTER the last denial is the human disposing of it — the supervised path
    # working exactly as designed. Only a grant that came *later* counts; an envelope that
    # had already expired before the denial is what caused it.
    granted_after = any(
        e.get("type") == "spend_authorized" and e.get("ts", "") >= last_denial
        for e in events
    )
    return [] if granted_after else denials


def _bump_fires() -> int:
    try:
        count = int(FIRE_COUNT.read_text().strip()) if FIRE_COUNT.exists() else 0
    except (OSError, ValueError):
        count = 0
    count += 1
    try:
        FIRE_COUNT.parent.mkdir(parents=True, exist_ok=True)
        FIRE_COUNT.write_text(str(count))
    except OSError:
        pass
    return count


REPORT = """SPEND-BACKSTOP: {n} spend denial(s) this session, and none was dispositioned.

{lines}

A denial must end in one of two places:
  • a human granted an envelope   →  bin/tessera-authorize grant --usd <n> --ttl <t> --note "..."
  • an escalation packet was raised →  bin/tessera-escalate raise --category spend_unauthorized \\
        --summary "<what is stuck>" --tried "<what you attempted, and how it failed>" \\
        --option "<what a human should choose between>"

Neither happened, so the block left no trace a human will ever see. If you routed around it —
did other work, took an offline path, or dropped it — that is the failure this hook exists to
catch: unsupervised, nobody learns the envelope was wrong.

If the denial was a FALSE POSITIVE (the command committed no spend — a grep, a commit message,
a file write), say so plainly and finish. That is a legitimate disposition, and it is a finding
about the guard's patterns, not about you.
"""


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return 0
    session_id = argv[1]

    denials = undispositioned(_events(session_id), _escalated(session_id))
    if not denials:
        return 0
    if _bump_fires() > MAX_FIRES:
        return 0  # a backstop that can wedge a session gets ripped out, and then protects nothing

    lines = "\n".join(
        f"  • {e.get('data', {}).get('command', '?')[:90]}" for e in denials[:5]
    )
    sys.stderr.write(REPORT.format(n=len(denials), lines=lines))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
