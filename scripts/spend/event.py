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


def _logs_dir() -> Path:
    """`.tessera/logs` under the REPO, never the cwd.

    Was `Path(".tessera/logs")` — relative. `guard.py` runs on PreToolUse(Bash), so its
    cwd is the session cwd, which any `cd` moves: a `spend_denied` emitted after the agent
    changed directory landed in a subdirectory and was invisible to every reader anchored
    at the repo root. **This is the same bug `bin/tessera-degraded` records having fixed
    for the REPORTER on 2026-07-26** — *"the spend guard's fail-open was correctly
    reported, into `scripts/.tessera/logs/`, where P13 will never see it"* — and the
    audit writer for the spend control itself was left behind. Fix the pattern, not the
    row (#11). Measured 2026-08-10: from a temp cwd, `emit()` returned a relative path
    and wrote outside the repo.

    Local rather than imported from `gate.paths`: consolidating the three channels'
    writers is a stated separate change (see this module's docstring and the
    `run-tests.sh` note on the emit.py/scan.py collision), and a live anchoring bug should
    not wait on it. `gate.paths.safe_session_id` is the canonical sanitiser; this mirrors
    its behaviour deliberately, and the duplication is the cost of that deferral."""
    override = os.environ.get("TESSERA_ROOT")
    base = Path(override) if override else Path(__file__).resolve().parents[2]
    return base / ".tessera" / "logs"


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
    path = _logs_dir() / f"{Path(session_id).name or 'unknown'}.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        event = build_event(event_type, data, session_id=session_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        return path
    except OSError:
        return None
