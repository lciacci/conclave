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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_event(
    fired: bool,
    kind: str,
    note: str | None = None,
    *,
    session_id: str,
    ts: str | None = None,
) -> dict:
    """The contract-shaped event. should_fire null, score/threshold absent."""
    data: dict = {
        "fired": fired,
        "suggestion_kind": kind,
        "should_fire": None,
    }
    if note:
        data["note"] = note
    return {
        "type": "suggestion_gate",
        "ts": ts or _utc_now_iso(),
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
    p.add_argument("--kind", required=True, help="suggestion category, e.g. refactor/compact")
    p.add_argument("--note", default=None, help="free text: what was proposed")
    p.add_argument("--dry-run", action="store_true", help="print event, do not append")
    args = p.parse_args(argv)

    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session_id:
        print("CLAUDE_CODE_SESSION_ID not set; cannot key the event", file=sys.stderr)
        return 2

    event = build_event(args.fired, args.kind, args.note, session_id=session_id)
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
