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

# ── Runs on ANY python3, including macOS's /usr/bin/python3 (3.9). LOAD-BEARING. ──────────
# This hook must keep working when the venv is broken, so it runs on bare `python3`. On
# 2026-07-12 that reasoning was proved half-right, and dangerously: **stdlib-only is NOT
# version-independent.** When the interpreter NAME drifts, the VERSION drifts with it. On a
# /usr/bin-first PATH `python3` is 3.9; PEP-604 annotations (`str | None`) raise TypeError at
# definition time; the script exits 1; and the hook wrapper passes that through as "not 2",
# i.e. **ALLOW**. An unauthorized GPU boot proceeded, silently.
# `from __future__ import annotations` makes annotations lazy strings. DO NOT REMOVE.
from __future__ import annotations
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


SPEND_CATEGORIES = ("spend_unauthorized", "spend")


def _escalated(session_id: str, root: Path | None = None) -> bool:
    """Did this session raise a SPEND-shaped packet?

    TIGHTENED 2026-07-27 (ADR-0016). This used to clear on any packet at all, reasoning that
    "an agent that escalated *something* while blocked has not silently routed around." The
    claim is defensible and the effect was a bypass nobody chose: a session that raises an
    unrelated escalation silences its spend backstop by accident, and this repo raises packets
    for all sorts of things. A denial is answered by a packet ABOUT the denial.
    """
    for path in (root or ESCALATIONS).glob("*.json"):
        try:
            packet = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if packet.get("session_id") != session_id:
            continue
        category = str(packet.get("category", "")).lower()
        if any(category.startswith(c) for c in SPEND_CATEGORIES):
            return True
    return False


def undispositioned(events: list[dict], escalated: bool) -> list[dict]:
    """The denials that ended nowhere. Empty list == nothing to answer for."""
    if escalated:
        return []
    denials = [e for e in events if e.get("type") == "spend_denied"]
    if not denials:
        return []
    last_denial = max(e.get("ts", "") for e in denials)
    # A dismissal AFTER the last denial is the third disposition (ADR-0016): a human saying
    # the guard was wrong. Same "after" rule as a grant — a dismissal recorded before a later
    # denial says nothing about that denial.
    dismissed_after = any(
        e.get("type") == "spend_dismissed" and e.get("ts", "") >= last_denial
        for e in events
    )
    if dismissed_after:
        return []
    # A grant AFTER the last denial is the human disposing of it — the supervised path
    # working exactly as designed. Only a grant that came *later* counts; an envelope that
    # had already expired before the denial is what caused it.
    granted_after = any(
        e.get("type") == "spend_authorized" and e.get("ts", "") >= last_denial
        for e in events
    )
    return [] if granted_after else denials


KEEP_SESSIONS = 20


def _read_fires() -> dict:
    """{session_id: count}. Anything unreadable or legacy reads as EMPTY, on purpose.

    A parse failure must leave the backstop ENABLED. This mechanism's whole job is to notice
    a spend denial that vanished; failing toward "already capped, stay quiet" would make a
    corrupt file into a silent kill switch, which is the bug below one level down.
    """
    try:
        data = json.loads(FIRE_COUNT.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _bump_fires(session_id: str) -> int:
    """Count fires PER SESSION, not for all time.

    THE BUG THIS FIXES (found 2026-07-27, and it had been live for a long time): this file
    held a single global integer that nothing ever reset. `MAX_FIRES` was written as
    loop-safety — "a backstop that can wedge a session gets ripped out, and then protects
    nothing" — which is a statement about ONE session. Against a monotonic global counter it
    became a permanent kill switch: past 3 fires ever, `main()` returned 0 forever. It was
    found at **47**, i.e. the spend backstop had been silently dead, and `rc=0` looks exactly
    like "nothing to report".

    Almost certainly self-inflicted by the sibling bug fixed the same day: every
    `bin/tessera-test` run under a real session wrote four undispositioned `spend_denied`
    events, each of which bumped this counter at the next Stop. The suite burned through the
    cap of the mechanism that guards unsupervised spend.

    Keyed by session, so the cap does the job it was written for and cannot outlive the
    session it was protecting. A clean session never bumps at all; a new session starts at
    zero because its key is absent. Old keys are pruned so this cannot grow forever.
    """
    fires = _read_fires()
    count = int(fires.pop(session_id, 0)) + 1
    fires[session_id] = count  # re-inserted last: dict order is recency, and JSON keeps it
    # Prune the OLDEST keys, not the smallest counts. The first version sorted by count and
    # evicted the session it had just recorded — the freshest entry is also the lowest one.
    for stale in list(fires)[:max(0, len(fires) - KEEP_SESSIONS)]:
        del fires[stale]
    try:
        FIRE_COUNT.parent.mkdir(parents=True, exist_ok=True)
        FIRE_COUNT.write_text(json.dumps(fires))
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
    if _bump_fires(session_id) > MAX_FIRES:
        return 0  # a backstop that can wedge a session gets ripped out, and then protects nothing

    lines = "\n".join(
        f"  • {e.get('data', {}).get('command', '?')[:90]}" for e in denials[:5]
    )
    sys.stderr.write(REPORT.format(n=len(denials), lines=lines))
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
