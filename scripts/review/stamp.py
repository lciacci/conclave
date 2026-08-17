#!/usr/bin/env python3
"""Record what a code review actually saw, so "changed since" is answerable at push time.

WHY THIS EXISTS. conclave F-004: "the review gate covers the draft and never the fix." The edits
made in response to a review are the highest-risk part of a change — written under time pressure,
in areas already known to be delicate, by the author who got them wrong once — and in the common
sequence review -> fix -> commit -> push, nothing ever reviews them.

WHAT THE EVIDENCE ACTUALLY SUPPORTS, which is narrower than F-004 claims. Measured on the
2026-08-17 session: three review rounds, and rounds 1 and 2 WERE re-reviewed, because each round
targeted `origin/main..HEAD` — the accumulating unpushed range — rather than the working diff.
Only the LAST round's fixes escaped, and only because they were pushed. So the hole is the
TERMINAL fix, and the variable that creates it is the push, not the fixing.

WHY THIS IS NOT A BLOCKING GATE, deliberately, and against F-004's own suggested fix (1). "Your
most recent edits are unreviewed" is true by construction every time you stop, so a hook firing on
it detects the end of a session rather than a defect. This repo has already priced that lesson:
P13 has no acknowledgment state, fires on correct-but-spent conditions, and G-a fires on its
streak with no right answer available. A signal that is always true is one you learn to dismiss.
It also regresses — every re-review leaves its own fixes unreviewed — so there is no fixed point
to gate on.

WHAT IT DOES INSTEAD. Records the range a review covered; `.githooks/pre-push` reports the set
difference at the outward boundary. A set difference terminates where a recursion does not, and
the output is a fact ("3 files changed since the review saw them") for a human to dispose of.
Warn-only — the posture ADR-0012 already set for sqlfluff.

KNOWN GAP, found the day this shipped: a stamp records the range a review was TOLD to cover,
not the range the author meant. On 2026-08-17 `/code-review high 0b27332` was invoked meaning
"since that commit"; the skill read it as "that commit" and returned six findings, all correct and
all already fixed one commit later. Nothing here would have caught that — the stamp would have
faithfully recorded the wrong scope. It is the same certified-at/changed-since gap one level up:
the certifier's own scope is unverified. Cheap partial remedy if it recurs: have the reviewer
report the ref range it resolved, and compare that against the stamp.

Stdlib-only: imported by a git hook that runs without the venv.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
EVENT = "review_stamped"


def _log_path() -> Path:
    session = os.environ.get("CLAUDE_CODE_SESSION_ID", "manual")
    d = ROOT / ".tessera" / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{session}.jsonl"


def _git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=15)
    except Exception:
        return ""
    return out.stdout.strip()


def record(base: str = "origin/main") -> int:
    """Stamp the review's coverage: the HEAD it saw and the files in that range."""
    head = _git("rev-parse", "HEAD")
    if not head:
        print("not a git repo, or no HEAD", file=sys.stderr)
        return 1
    files = [f for f in _git("diff", "--name-only", f"{base}...HEAD").splitlines() if f]
    dirty = [f for f in _git("diff", "--name-only").splitlines() if f]
    event = {
        "type": EVENT,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_id": os.environ.get("CLAUDE_CODE_SESSION_ID", "manual"),
        "source": "review-stamp",
        "data": {"base": base, "head": head, "files": sorted(set(files + dirty)),
                 "had_uncommitted": bool(dirty)},
    }
    with _log_path().open("a") as fh:
        fh.write(json.dumps(event) + "\n")
    print(f"review stamped: {head[:12]} over {len(event['data']['files'])} file(s) vs {base}")
    return 0


def latest() -> "dict | None":
    """The newest stamp across all session logs. None when no review is on record."""
    best = None
    logs = ROOT / ".tessera" / "logs"
    if not logs.is_dir():
        return None
    for f in sorted(logs.glob("*.jsonl")):
        try:
            lines = f.read_text().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") == EVENT and (best is None or e.get("ts", "") > best.get("ts", "")):
                best = e
    return best


def changed_since_review(base: str = "origin/main") -> "tuple[dict | None, list[str]]":
    """(stamp, files in the outgoing range that changed after the stamp's HEAD)."""
    stamp = latest()
    if stamp is None:
        return None, []
    head = stamp["data"]["head"]
    if not _git("cat-file", "-t", head):
        return stamp, []            # stamped commit is gone (rebase); nothing honest to say
    return stamp, [f for f in _git("diff", "--name-only", f"{head}..HEAD").splitlines() if f]


def _main(argv: "list[str]") -> int:
    if any(a in ("-h", "--help") for a in argv):
        print("usage: stamp.py [BASE_REF]   (default origin/main)\n\n"
              "Records what a code review covered. Run AFTER a review completes.\n"
              "`.githooks/pre-push` reports what changed since.")
        return 0
    args = [a for a in argv if not a.startswith("-")]
    base = args[0] if args else "origin/main"
    # A base that does not resolve produced a stamp claiming coverage vs `--help` on the first
    # run. A stamp is a claim about SCOPE; a claim against a ref that does not exist is worse
    # than no claim, because `changed_since_review` will happily diff from it.
    if not _git("rev-parse", "--verify", "--quiet", base):
        print(f"base ref {base!r} does not resolve — refusing to stamp", file=sys.stderr)
        return 2
    return record(base)


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
