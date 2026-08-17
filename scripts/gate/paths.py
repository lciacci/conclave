#!/usr/bin/env python3
"""Repo-root anchoring for the DIRECTLY-INVOKED gate tools.

These read and write `.tessera/logs/<session>.jsonl`, keyed by CLAUDE_CODE_SESSION_ID.
That makes the log a SESSION artifact, not a cwd artifact — so resolving it against the
cwd is wrong by construction. Run by hand from a foreign cwd (the Bash tool keeps cwd
across calls) it splits: 2026-07-24's 4/2 gate-log split was exactly that.

Their hook-invoked siblings (`scan.py`, `verify/scan.py`, `spend/*`) are safe only
because the wrapper shell does `cd "$(dirname "$0")/../.."` first. These have no wrapper.

**Anchored off `__file__`, deliberately NOT off CLAUDE_PROJECT_DIR.** That variable is
set for hook processes but is UNSET in the Bash tool's environment, so the
`${CLAUDE_PROJECT_DIR:-.}` form the hook *commands* use collapses to `.` here and buys
nothing. `__file__` is a fact the script always has.

The rule this encodes: **the anchor must match the key.** Session-keyed artifacts anchor
to the repo; repo-keyed tools (`bin/tessera-*`, `tessera_config.py`) correctly follow cwd
— `tessera-watch` run inside a downstream *should* evaluate that downstream.

Stdlib-only on purpose (CLAUDE.md's interpreter split): these run under bare `python3`.
"""

from __future__ import annotations

import os
from pathlib import Path


def root() -> Path:
    """The repo this script belongs to. TESSERA_ROOT overrides.

    Read at call time, not import time, so a test can set it per-case. The override is
    not only test scaffolding — it is also the honest way to point a gate tool at another
    checkout on purpose, which is otherwise indistinguishable from the cwd-drift bug.
    """
    override = os.environ.get("TESSERA_ROOT")
    return Path(override) if override else Path(__file__).resolve().parents[2]


def logs_dir() -> Path:
    """`.tessera/logs/` under the anchored root."""
    return root() / ".tessera" / "logs"


def safe_session_id(session_id: str) -> str:
    """A session id used as a FILENAME, reduced to one path component.

    THE CANONICAL SANITISER. `docs/observatory.md` → "`CLAUDE_CODE_SESSION_ID` reaches a
    filename unsanitised in 7 modules" accepted that risk on the premise that the value is
    *only ever set by the harness*, with an explicit revisit trigger: any of these tools
    starting to take a session id from an argument, a config file, or a hook payload.
    **That trigger fired 2026-08-10** — `bin/tessera-degraded` takes it from a `--session`
    flag AND from the `session_id` field of piped JSON.

    The severity rejection still stands (every source is the harness or this repo's own
    hooks, so this is robustness, not a vulnerability). What changed is that the premise
    is no longer true, and the entry's own prescription was "one shared helper, applied to
    all — not a patch at the call site that changed". This is that helper.

    `Path(x).name` collapses `../../etc/passwd` to `passwd` and `/abs/path` to `path`;
    empty or all-separator input raises rather than silently writing to the directory
    itself."""
    cleaned = Path(session_id).name
    if not cleaned or cleaned in (".", ".."):
        raise ValueError(f"unusable session id: {session_id!r}")
    return cleaned


def log_path(session_id: str) -> Path:
    """This session's gate log."""
    return logs_dir() / f"{safe_session_id(session_id)}.jsonl"


def signals_path() -> Path:
    """`.mnemos/signals.jsonl` — same anchoring argument, different producer."""
    return root() / ".mnemos" / "signals.jsonl"


def demo() -> None:
    """Self-check: the anchor ignores cwd, and TESSERA_ROOT wins."""
    import tempfile

    repo = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        try:
            os.chdir(tmp)
            assert root() == repo, f"cwd leaked into the anchor: {root()}"
            assert logs_dir() == repo / ".tessera" / "logs"
            assert log_path("s1").name == "s1.jsonl"
            os.environ["TESSERA_ROOT"] = tmp
            assert root() == Path(tmp), "TESSERA_ROOT did not override"
        finally:
            os.environ.pop("TESSERA_ROOT", None)
            os.chdir(cwd)
    print("ok")


if __name__ == "__main__":
    demo()
