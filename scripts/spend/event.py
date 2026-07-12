#!/usr/bin/env python3
"""Spend-channel event writer.

Same shape as the gate and override channels (docs/design-principles.md: one JSON object
per line in `.tessera/logs/<session-id>.jsonl`, `type` / `ts` / `source` / `data`).

ponytail: gate/ and override/ each carry their own ~10-line copy of this. A shared writer
is the obvious cleanup, but it means touching three channels' import contracts (see the
run-tests.sh header on the emit.py/scan.py collision) — a separate change, not this one.

Contract: docs/contracts/spend-authorization.md
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

SOURCE = "spend-guard"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_event(event_type: str, data: dict, *, session_id: str, ts: str | None = None) -> dict:
    return {
        "type": event_type,
        "ts": ts or _utc_now_iso(),
        "session_id": session_id,
        "source": SOURCE,
        "data": data,
    }


def emit(event_type: str, data: dict) -> Path | None:
    """Append an event. Returns the path written, or None if there is no session to key on.

    Never raises: an audit-log failure must never change a spend decision.
    """
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not session_id:
        return None
    path = Path(".tessera/logs") / f"{session_id}.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        event = build_event(event_type, data, session_id=session_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return path
    except OSError:
        return None
