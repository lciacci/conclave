#!/usr/bin/env python3
"""Stop-hook gate-scan backstop — counts gate-shaped turns, compares to gates logged.

Principle #17: the surface-decisions *record* (emit.py) rode pure model recall and
missed ~85% of gates under real work (observatory, n=2). This makes the trigger the
harness instead: the Stop hook counts assistant turns that stopped to ask, diffs
against `.tessera/logs/<session>.jsonl`, and on a gap exits 2 so the model must
adjudicate before finishing.

Detection is a deliberately RECALL-oriented net (structural, per decision 1a): an
assistant run that ended on a question and handed back to a human. It over-counts —
clarifying questions look like gates. That is by design. The model is the precision
filter on the exit-2 turn; the hook only guarantees the turn happens.

Fires when: gap >= 2, OR nothing was logged at all (a 1-surfaced/0-logged session is
a 100% miss, and leaves no log file — invisible to ratio.py. This is the one case
the backstop exists to see).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

LOGS = Path(".tessera/logs")
GAP_THRESHOLD = 2
TAIL_CHARS = 300  # a '?' this near the end of a turn reads as "stopped to ask"
PREVIEW_CHARS = 100
MAX_FIRES_PER_SESSION = 3  # never nag unboundedly; a wedged hook gets ripped out
TURN_ID_CHARS = 12  # sha256 prefix; collision-irrelevant at a few dozen turns/session


def iter_entries(path: str):
    """Transcript JSONL, main-thread only. Bad lines are skipped, never fatal."""
    try:
        with open(path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not d.get("isSidechain"):
                    yield d
    except OSError:
        return


def is_human_turn(entry: dict) -> bool:
    """A real user message — not a tool_result carrier (those are also role=user)."""
    if entry.get("type") != "user":
        return False
    return isinstance((entry.get("message") or {}).get("content"), str)


def assistant_text(entry: dict) -> str:
    """Concatenated text blocks of one assistant entry ('' if it's thinking/tool_use)."""
    if entry.get("type") != "assistant":
        return ""
    content = (entry.get("message") or {}).get("content")
    if not isinstance(content, list):
        return ""
    return "\n".join(b.get("text", "") for b in content if b.get("type") == "text")


def _is_asking(text: str) -> bool:
    return "?" in text[-TAIL_CHARS:]


def _preview(text: str) -> str:
    tail = " ".join(text.split())[-PREVIEW_CHARS:]
    return tail.strip()


def turn_id(text: str) -> str:
    """Content address for one asking turn — stable across scans of a growing transcript.

    Hashes the WHOLE normalized turn, not the preview: two turns can share a 100-char
    tail (this repo's asking turns often end "…, OK to proceed?") and collapsing them
    would silently suppress a real gate. Whitespace-normalized so reflow cannot change
    the id of an unchanged turn.
    """
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:TURN_ID_CHARS]


def find_asking_turns(path: str) -> list[tuple[str, str]]:
    """(turn_id, preview) for assistant runs that asked, then handed back to a human.

    An assistant run reaching a human turn means it stopped rather than tool-called
    (a tool_use is followed by a tool_result, which is not a human turn), so the
    structure alone carries "proposed, then waited" — no NLP needed.

    ANY text block in the run may carry the question, not just the last: a model that
    asks, then tool-calls, then signs off with a statement has still surfaced a gate.
    Checking only the final block silently missed exactly that shape.
    """
    turns, asking_blocks = [], []
    for entry in iter_entries(path):
        text = assistant_text(entry)
        if text:
            if _is_asking(text):
                asking_blocks.append(text)
        elif is_human_turn(entry):
            if asking_blocks:
                chosen = asking_blocks[-1]
                turns.append((turn_id(chosen), _preview(chosen)))
            asking_blocks = []
    return turns


def load_dispositions(session_id: str) -> set[str]:
    """Turn ids already adjudicated 'not a gate' — conclave F-001.

    The scan over-counts on purpose and the model is the precision filter. But the
    ruling was thrown away: turns dispositioned as narration, a retry, or an
    investigation were re-flagged on EVERY subsequent Stop, so each Stop re-litigated
    closed decisions instead of surfacing only what was new. Conclave hit this ~4x in
    one session. Persisting the ruling makes adjudication monotonic without weakening
    detection — the net stays exactly as wide, it just stops asking twice.
    """
    path = LOGS / f"{session_id}.jsonl"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return set()
    disposed: set[str] = set()
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "gate_disposition":
            continue
        for tid in event.get("data", {}).get("turn_ids", []):
            disposed.add(tid)
    return disposed


def count_logged(session_id: str) -> int:
    """suggestion_gate events for this session. Other event types are not gates."""
    path = LOGS / f"{session_id}.jsonl"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return 0
    total = 0
    for line in lines:
        try:
            if json.loads(line).get("type") == "suggestion_gate":
                total += 1
        except json.JSONDecodeError:
            continue
    return total


def should_fire(surfaced: int, logged: int) -> bool:
    if surfaced == 0:
        return False
    if logged == 0:
        return True
    return (surfaced - logged) >= GAP_THRESHOLD


def _fire_count(session_id: str) -> int:
    marker = LOGS / f".scan-fires-{session_id}"
    try:
        return int(marker.read_text().strip())
    except (OSError, ValueError):
        return 0


def _bump_fires(session_id: str) -> None:
    marker = LOGS / f".scan-fires-{session_id}"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(str(_fire_count(session_id) + 1))
    except OSError:
        pass


def report(turns: list[tuple[str, str]], logged: int, suppressed: int = 0) -> str:
    note = f" ({suppressed} already ruled not-a-gate, suppressed)" if suppressed else ""
    lines = [
        f"GATE-SCAN: {len(turns)} gate-shaped turn(s) detected, {logged} logged "
        f"via scripts/gate/emit.py{note}.",
        "",
        "Turns that stopped to ask (tail of each):",
    ]
    lines += [f"  [{tid}] …{preview}" for tid, preview in turns]
    lines += [
        "",
        "Log the gates you surfaced but did not record (--retro is required here:",
        "it marks the ts as adjudication time, so passive labelers skip the event):",
        '  python3 scripts/gate/emit.py --fired --retro --kind <kind> --note "<what you proposed>"',
        "  kinds: design | scope | sequencing | process | finding | doc | outward",
        "",
        "Detection over-counts on purpose — you are the precision filter. For turns that",
        "were NOT gates (clarifying questions, narration), record the ruling so it is not",
        "re-asked every Stop — pass the [id]s shown above:",
        '  python3 scripts/gate/emit.py --not-a-gate --turn <id> [--turn <id> ...] --note "why"',
        "",
        "Then finish your response normally.",
    ]
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) < 3:
        return 0
    transcript, session_id = sys.argv[1], sys.argv[2]
    if _fire_count(session_id) >= MAX_FIRES_PER_SESSION:
        return 0
    detected = find_asking_turns(transcript)
    disposed = load_dispositions(session_id)
    turns = [t for t in detected if t[0] not in disposed]
    suppressed = len(detected) - len(turns)
    logged = count_logged(session_id)
    if not should_fire(len(turns), logged):
        return 0
    _bump_fires(session_id)
    print(report(turns, logged, suppressed), file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # fail open, always — a backstop must never wedge a session
        sys.exit(0)
