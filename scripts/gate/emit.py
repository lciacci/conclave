#!/usr/bin/env python3
"""Suggestion-gate event recorder (model-emitted producer).

Claude calls this at each gate moment (principle #12: Claude proposes, user
disposes) to append one `suggestion_gate` event to the session's event log,
per docs/contracts/gate-event.md.

    python3 scripts/gate/emit.py --fired   --kind refactor --note "make contract canonical"
    python3 scripts/gate/emit.py --held    --kind compact  --note "context still small"

`should_fire` is left null — labeled post-hoc in the dashboard. `score`/`threshold`
are omitted (no scoring heuristic; we don't invent numbers). Session id comes from
CLAUDE_CODE_SESSION_ID so the event correlates with the dashboard's session data.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


# Controlled vocabulary (spec 15). 102 events under a free-text kind produced 33
# distinct values, mostly singletons ("design" ×5 spellings) — unsliceable. The
# contract's "promote to an enum when the kinds stabilize" clause triggered on
# the opposite evidence: they diverged. Fail-closed at emit is safe HERE only —
# emit is model-interactive, the model re-runs with a valid kind in-turn.
KINDS = ("design", "scope", "sequencing", "process", "finding", "doc", "outward")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_event(
    fired: bool,
    kind: str,
    note: str | None = None,
    *,
    session_id: str,
    ts: str | None = None,
    retro: bool = False,
) -> dict:
    """The contract-shaped event. should_fire null, score/threshold absent.

    retro=True marks an event logged after the fact (gate-scan adjudication):
    its ts is emit time, not the gate moment, so a timestamp-join to the user's
    disposition is invalid — any passive labeler must skip it (the 2026-07-20
    backfill mislabeled these wholesale before the flag existed).
    """
    data: dict = {
        "fired": fired,
        "suggestion_kind": kind,
        "should_fire": None,
    }
    if note:
        data["note"] = note
    if retro:
        data["retro"] = True
    return {
        "type": "suggestion_gate",
        "ts": ts or _utc_now_iso(),
        "session_id": session_id,
        "source": "suggestion-gate-recorder",
        "data": data,
    }


def build_disposition(turn_ids: list[str], note: str | None, *, session_id: str) -> dict:
    """A 'not a gate' ruling on specific transcript turns — conclave F-001.

    The gate-scan over-counts by design and the model is the precision filter, but the
    ruling was discarded: turns already dispositioned as narration or a clarifying
    question were re-flagged on every subsequent Stop, so each Stop re-litigated closed
    decisions. This persists the ruling. Detection is unchanged — the net stays exactly
    as wide, it just stops asking twice.

    Keyed by turn id (content hash from scan.py), NOT by index: transcripts grow between
    Stops, so any positional key would slide.
    """
    data: dict = {"verdict": "not-a-gate", "turn_ids": turn_ids}
    if note:
        data["note"] = note
    return {
        "type": "gate_disposition",
        "ts": _utc_now_iso(),
        "session_id": session_id,
        "source": "suggestion-gate-recorder",
        "data": data,
    }


def _log_path(session_id: str) -> Path:
    return Path(".tessera/logs") / f"{session_id}.jsonl"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Append a suggestion_gate event.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--fired", action="store_true", help="gate surfaced the suggestion")
    g.add_argument("--held", action="store_true", help="gate withheld the suggestion")
    g.add_argument("--not-a-gate", dest="not_a_gate", action="store_true",
                   help="record that specific scanned turns were NOT gates (needs --turn)")
    p.add_argument("--kind", default=None,
                   help=f"suggestion category, one of: {'/'.join(KINDS)}")
    p.add_argument("--turn", action="append", default=[], metavar="ID",
                   help="turn id from the gate-scan report; repeatable (--not-a-gate only)")
    p.add_argument("--note", default=None, help="free text: what was proposed")
    p.add_argument("--retro", action="store_true",
                   help="logged after the fact (scan adjudication) — ts is not the gate moment")
    p.add_argument("--dry-run", action="store_true", help="print event, do not append")
    args = p.parse_args(argv)

    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session_id:
        print("CLAUDE_CODE_SESSION_ID not set; cannot key the event", file=sys.stderr)
        return 2

    if args.not_a_gate:
        if not args.turn:
            print("--not-a-gate needs at least one --turn <id> (ids are in the "
                  "gate-scan report)", file=sys.stderr)
            return 2
        event = build_disposition(args.turn, args.note, session_id=session_id)
    else:
        if not args.kind:
            print("--kind is required with --fired/--held", file=sys.stderr)
            return 2
        if args.kind not in KINDS:
            print(f"unknown --kind '{args.kind}' — use one of: {', '.join(KINDS)} "
                  f"(docs/contracts/gate-event.md)", file=sys.stderr)
            return 2
        event = build_event(args.fired, args.kind, args.note,
                            session_id=session_id, retro=args.retro)
    line = json.dumps(event, ensure_ascii=False)

    if args.dry_run:
        print(line)
        return 0

    path = _log_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(f"gate event appended → {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
