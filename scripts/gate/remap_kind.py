#!/usr/bin/env python3
"""One-shot suggestion_kind remap (spec 15) — and the executable record of it.

The free-text era left 33 distinct kinds across 102 events. This rewrites each
gate event's `suggestion_kind` to the 7-kind vocabulary (emit.KINDS), keeping
the original in `suggestion_kind_raw` when it differed — the logs are gitignored
runtime state, so the raw value has no other backup. Idempotent: an event whose
kind is already canonical (and any event already carrying `_raw`) is untouched.
Unknown kinds are reported, never guessed.

Run from repo root: python3 -m scripts.gate.remap_kind [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

try:
    from scripts.gate.emit import KINDS
except ModuleNotFoundError:  # run as a loose script from repo root
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.gate.emit import KINDS

LOGS = Path(".tessera/logs")

# The merge table — docs/contracts/gate-event.md carries the human-readable copy.
MAP = {
    "design": "design", "design-decision": "design", "design-approach": "design",
    "approach": "design", "design-direction": "design", "structural": "design",
    "design_choice": "design", "decision": "design", "feature": "design",
    "implementation": "design", "spec-fold": "design",
    "scope": "scope", "prune": "scope", "cleanup": "scope",
    "next-work": "sequencing", "sequencing": "sequencing", "priority": "sequencing",
    "process": "process", "trial-protocol": "process", "trial-verdict": "process",
    "check-add": "process", "curation-policy": "process", "provenance": "process",
    "finding": "finding", "bug": "finding",
    "doc": "doc", "doc-fix": "doc", "doc-scope": "doc",
    "commit": "outward", "action": "outward", "outward-action": "outward",
    "release": "outward", "global-layer-refresh": "outward",
}


def remap_line(line: str) -> tuple[str, str | None]:
    """(new_line, change) — change is 'old→new', 'unknown:<kind>', or None."""
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        return line, None
    if ev.get("type") != "suggestion_gate":
        return line, None
    data = ev.get("data") or {}
    kind = data.get("suggestion_kind")
    if kind in KINDS or "suggestion_kind_raw" in data:
        return line, None
    if kind not in MAP:
        return line, f"unknown:{kind}"
    data["suggestion_kind"] = MAP[kind]
    data["suggestion_kind_raw"] = kind
    ev["data"] = data
    return json.dumps(ev, ensure_ascii=False), f"{kind}→{MAP[kind]}"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Remap legacy suggestion_kind values.")
    p.add_argument("--dry-run", action="store_true", help="tally only, write nothing")
    args = p.parse_args(argv)

    tally: Counter[str] = Counter()
    for f in sorted(LOGS.glob("*.jsonl")):
        lines = f.read_text().splitlines()
        out, changed = [], False
        for line in lines:
            new, change = remap_line(line)
            out.append(new)
            if change:
                tally[change] += 1
                changed = changed or not change.startswith("unknown:")
        if changed and not args.dry_run:
            f.write_text("\n".join(out) + "\n")
    for change, n in sorted(tally.items()):
        print(f"{n:3d}  {change}")
    print(f"{'DRY-RUN — ' if args.dry_run else ''}"
          f"{sum(n for c, n in tally.items() if not c.startswith('unknown:'))} remapped, "
          f"{sum(n for c, n in tally.items() if c.startswith('unknown:'))} unknown left as-is",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
