#!/usr/bin/env python3
"""tessera-authorize — grant a run-scoped external-spend envelope (spec 06).

This is what converts conclave from supervisable-only to unsupervised. Fourteen of its
twenty-five recorded gates were a human saying yes to one specific GPU boot. An envelope
collapses those into a single up-front authorization: *this run may commit spend, for
this long, up to about this much.*

    tessera-authorize grant --usd 20 --ttl 4h --note "chunk 4 judge eval"
    tessera-authorize show                    # exit 1 if no live envelope
    tessera-authorize revoke

**The TTL is what is enforced; the dollar figure is not.** Tessera cannot meter dollars —
AWS can, and does (conclave's budget.tf + hardstop.tf). `--usd` is recorded for audit and
shown to the agent as context; the honest bound Tessera holds is time. For a GPU, cost is
~linear in runtime, so a time-boxed envelope is a real spend bound — and the AWS monthly
cap is the backstop if the estimate is wrong.

Contract: docs/contracts/spend-authorization.md
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from event import emit
from guard import AUTH_PATH, is_live, load_auth

TTL_RE = re.compile(r"^(\d+)([mh])$")


def parse_ttl(ttl: str) -> timedelta:
    """'4h' / '90m' -> timedelta. Rejects anything else rather than guessing."""
    m = TTL_RE.match(ttl.strip())
    if not m:
        raise ValueError(f"bad --ttl {ttl!r}: use e.g. 30m or 4h")
    value, unit = int(m.group(1)), m.group(2)
    if value <= 0:
        raise ValueError("--ttl must be positive")
    return timedelta(hours=value) if unit == "h" else timedelta(minutes=value)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def build_grant(usd: float, ttl: timedelta, note: str, now: datetime) -> dict:
    return {
        "granted_at": _iso(now),
        "expires_at": _iso(now + ttl),
        "usd": usd,
        "note": note,
        "granted_by": os.environ.get("USER", "unknown"),
        "session_id": os.environ.get("CLAUDE_CODE_SESSION_ID"),
    }


def cmd_grant(args, path: Path, now: datetime) -> int:
    try:
        ttl = parse_ttl(args.ttl)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    grant = build_grant(args.usd, ttl, args.note, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(grant, indent=2) + "\n")
    emit("spend_authorized", grant)
    print(f"granted ${args.usd} until {grant['expires_at']} ({args.ttl}) — {args.note}")
    print(f"  → {path}")
    return 0


def cmd_show(args, path: Path, now: datetime) -> int:
    auth = load_auth(path)
    if not is_live(auth, now):
        print("no live spend authorization — spend-committing commands are BLOCKED")
        return 1
    remaining = datetime.fromisoformat(auth["expires_at"].replace("Z", "+00:00")) - now
    minutes = int(remaining.total_seconds() // 60)
    print(f"LIVE — ${auth.get('usd')} · expires {auth['expires_at']} ({minutes}m left)")
    print(f"  note:       {auth.get('note')}")
    print(f"  granted_by: {auth.get('granted_by')}")
    return 0


def cmd_revoke(args, path: Path, now: datetime) -> int:
    if not path.exists():
        print("no authorization to revoke")
        return 0
    path.unlink()
    emit("spend_revoked", {"revoked_at": _iso(now)})
    print("revoked — spend-committing commands are BLOCKED")
    return 0


def main(argv: list[str] | None = None, path: Path | None = None) -> int:
    p = argparse.ArgumentParser(description="Grant a run-scoped external-spend envelope.")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("grant", help="authorize external spend for this run")
    g.add_argument("--usd", type=float, required=True, help="envelope, for audit + agent context")
    g.add_argument("--ttl", required=True, help="how long, e.g. 30m or 4h — THIS is enforced")
    g.add_argument("--note", required=True, help="what this run needs to boot, and why")
    g.set_defaults(fn=cmd_grant)

    sub.add_parser("show", help="show the live envelope (exit 1 if none)").set_defaults(fn=cmd_show)
    sub.add_parser("revoke", help="revoke immediately").set_defaults(fn=cmd_revoke)

    args = p.parse_args(argv)
    return args.fn(args, path or AUTH_PATH, datetime.now(timezone.utc))


if __name__ == "__main__":
    raise SystemExit(main())
