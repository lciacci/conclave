#!/usr/bin/env python3
"""Repo-root anchoring for the restore-receipt tools.

Same rule, same reasoning as `scripts/gate/paths.py` — read that file for the full
argument. In one line: **the anchor must match the key.** These write
`.tessera/logs/<session>.jsonl`, keyed by session, so resolving against cwd is wrong by
construction (2026-07-24 split the gate log 4/2 exactly that way, and 2026-07-26 let a
`cd` silence the spend guard).

Deliberately duplicated rather than imported from `scripts/gate/`: `run-tests.sh` runs
each of those directories in a SEPARATE pytest process because they carry colliding
module names (two `emit.py`, two `scan.py`, now three). A cross-directory import would
resolve differently under the suite than under a hook. Four lines of anchoring is a
cheaper liability than an import that works in one context and not the other.

Stdlib-only (CLAUDE.md's interpreter split): these run under bare `python3`.
"""

from __future__ import annotations

import os
from pathlib import Path


def root() -> Path:
    """The repo this script belongs to. TESSERA_ROOT overrides (tests, and the honest
    way to point at another checkout on purpose)."""
    override = os.environ.get("TESSERA_ROOT")
    return Path(override) if override else Path(__file__).resolve().parents[2]


def log_path(session_id: str) -> Path:
    """This session's event log — the channel gate/spend/override/degraded already share."""
    return root() / ".tessera" / "logs" / f"{session_id}.jsonl"


def demo() -> None:
    """Self-check: the anchor ignores cwd, and TESSERA_ROOT wins."""
    import tempfile

    repo = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory() as tmp:
        cwd = os.getcwd()
        try:
            os.chdir(tmp)
            assert root() == repo, f"cwd leaked into the anchor: {root()}"
            assert log_path("s1").name == "s1.jsonl"
            os.environ["TESSERA_ROOT"] = tmp
            assert root() == Path(tmp), "TESSERA_ROOT did not override"
        finally:
            os.environ.pop("TESSERA_ROOT", None)
            os.chdir(cwd)
    print("ok")


if __name__ == "__main__":
    demo()
