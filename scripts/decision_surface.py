#!/usr/bin/env python3
"""Surface the decisions that already govern a file, before it is edited.

WHY THIS EXISTS. 2026-07-24: a hook-anchoring fix was built without accounting for
ADR-0004's two-tier distribution, and would have cd'd every downstream hook to $HOME.
ADR-0004 was in docs/adr/, referenced from CLAUDE.md, and describes that exact failure
mode. It was read only after a human asked the right question. The same session named the
wrong scaffold template because it grepped for a filename instead of reading the ADR that
lists the right one.

That is principle #17 sitting on the design record itself: `tessera-watch-surface.sh`
makes the handoff land mechanically, but nothing makes a DECISION land. Which ADR is
relevant to the file you are about to touch rode pure model recall, and model recall lost.

SCOPE, STATED HONESTLY. This closes the FILE-ANCHORED half only. Measured at build time:
11/12 ADRs name at least one file, but only 20/43 observatory entries do. Decisions with
no file to key on — most of design-principles.md, every ADR's Alternatives Considered
(the "we already rejected that" knowledge), and the cross-entry through-lines — are
invisible here by construction. Those are the standing-patterns block's job, not this.

Stdlib-only on purpose (CLAUDE.md's interpreter split) so a bare python3 can run it.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from decision_amendments import build_amendments, render_amendments

ROOT = Path(__file__).resolve().parent.parent
MAX_DOCS = 3

# Backtick-quoted repo paths, anywhere in the body — NOT just the References section.
# ADR-0004 names templates/tessera/settings.base.json in its prose and in References;
# most of the signal in the observatory is in prose only. Restricting to References
# would have missed the very case that motivated this file.
_PATH = re.compile(r"`((?:bin|scripts|templates|docs|hooks|\.claude|_project_specs)/[A-Za-z0-9._/-]+)`")
_ADR_TITLE = re.compile(r"^#\s*(ADR-\d+):\s*(.+?)\s*$")
_STATUS = re.compile(r"^-\s*\*\*Status:\*\*\s*(.+?)\s*$", re.M)
_EXECUTED = re.compile(r"^-\s*\*\*Executed:\*\*\s*(.+?)\s*$", re.M)


def _execution_warning(body: str) -> str:
    """Loud ONLY when acting on the ADR is risky — i.e. it was decided and not (fully) built.

    A shipped ADR needs no annotation; an UNSHIPPED one read as settled is the failure this
    exists to stop. On 2026-07-26 a session read ADR-0008's verdict, acted on it, and the cut
    had been sitting unexecuted for 12 days. The amendment edge says "this was revisited";
    this says "this was never done".
    """
    m = _EXECUTED.search(body)
    if not m:
        return ""
    value = m.group(1).strip()
    low = value.lower()
    if low.startswith("not yet"):
        return "\u23f3 NOT EXECUTED \u2014 decided, never built. Do not act on it as settled."
    if low.startswith("partially"):
        return f"\u23f3 PARTIALLY EXECUTED \u2014 {value[len('partially'):].lstrip(' \u2014-')}"
    return ""


def _first_decision_line(body: str) -> str:
    """The ADR's own one-line summary: first prose sentence under ## Decision.

    Extraction, never summarisation — a generated gloss would be a second, driftable
    statement of the decision, which is the failure this repo keeps writing ADRs about.
    """
    m = re.search(r"^## Decision\s*\n(.*?)(?=^## )", body, re.S | re.M)
    if not m:
        return ""
    for line in m.group(1).splitlines():
        line = line.strip().lstrip("*-# ").strip()
        if len(line) > 30 and not line.startswith(("|", "```", ">")):
            return re.sub(r"\*\*|`", "", line)[:150]
    return ""


def build_index() -> dict[str, list[dict]]:
    """path-prefix -> [{doc, title, gloss, kind}]. Raises if the record is unreadable."""
    index: dict[str, list[dict]] = {}

    for adr in sorted((ROOT / "docs" / "adr").glob("0*.md")):
        body = adr.read_text()
        tm = _ADR_TITLE.match(body.splitlines()[0]) if body else None
        if not tm:
            continue
        sm = _STATUS.search(body)
        entry = {
            "doc": f"docs/adr/{adr.name}",
            "title": f"{tm.group(1)} ({sm.group(1) if sm else '?'}) {tm.group(2)}",
            "gloss": _first_decision_line(body),
            "kind": "adr",
            "sort": adr.name,
            "execution": _execution_warning(body),
        }
        for path in set(_PATH.findall(body)):
            index.setdefault(path, []).append(entry)

    obs = ROOT / "docs" / "observatory.md"
    if obs.exists():
        # Split on ### so each entry's paths attach to that entry, not the whole file.
        for chunk in re.split(r"^### ", obs.read_text(), flags=re.M)[1:]:
            title = chunk.splitlines()[0].strip()
            entry = {"doc": "docs/observatory.md", "title": title,
                     "gloss": "", "kind": "observatory", "sort": "z"}
            for path in set(_PATH.findall(chunk)):
                index.setdefault(path, []).append(entry)
    return index


def lookup(target: str, index: dict[str, list[dict]]) -> list[dict]:
    """Documents governing `target`, by prefix — an ADR naming .claude/scripts/ covers
    every script under it, which is exactly the generalisation that was missed."""
    hits, seen = [], set()
    for path, entries in index.items():
        if target == path or target.startswith(path.rstrip("/") + "/") or path.startswith(target + "/"):
            for e in entries:
                key = (e["doc"], e["title"])
                if key not in seen:
                    seen.add(key)
                    hits.append(e)
    hits.sort(key=lambda e: (e["kind"] != "adr", e["sort"]), reverse=False)
    return hits[:MAX_DOCS]


def render(target: str, hits: list[dict], amendments: dict[str, list[str]] | None = None) -> str:
    amendments = amendments or {}
    out = [f"DECISION SURFACE — {target}"]
    for h in hits:
        out.append(f"  {h['title']}")
        if h["gloss"]:
            out.append(f"    → {h['gloss']}")
        # The amendment edge lives in decision_amendments.py — see its docstring.
        if h.get("execution"):
            out.append(f"    {h['execution']}")
        if h["kind"] == "adr":
            out += render_amendments(h["title"].split()[0], amendments)
    out.append(f"  Read before editing: {', '.join(sorted({h['doc'] for h in hits}))}")
    return "\n".join(out)


def relative(target: str) -> str:
    """Repo-relative path, case-insensitively (F-002).

    macOS's case-insensitive FS hands back whatever casing the caller's cwd used —
    `/Users/.../claude/tessera` vs `.../Claude/tessera`. `Path.resolve()` does NOT
    canonicalise case, so a plain `relative_to(ROOT)` raises on the mismatch, and a
    naive fallback returns the absolute path — which is never an index key, so the
    hook goes silently blank for half of all sessions depending on cwd casing. That is
    the fail-open class (standing pattern #2) on the very hook meant to prevent it.
    """
    rt = str(Path(target).resolve())
    root = str(ROOT)
    if rt.lower().startswith(root.lower() + "/"):
        return rt[len(root) + 1:]
    return target


def emit_hook(target: str) -> None:
    """Emit a PreToolUse `additionalContext` envelope — the ONLY channel that reaches the model.

    A PreToolUse hook's plain stdout on exit 0 goes to the debug log, NOT into context
    (verified against code.claude.com/docs/en/hooks; the exceptions are SessionStart /
    UserPromptSubmit). The original build printed bare text and was silent to its whole
    audience — the fail-open class on the very hook meant to defeat it. So: JSON envelope.

    The error path also routes through additionalContext, not stderr: this hook is ADVISORY
    and must never block an edit, so exit 2 (the other loud channel) is wrong. A wrong-but-
    visible message beats a silent one — the message just rides the same channel as a hit.
    """
    rel = relative(target)
    try:
        hits = lookup(rel, build_index())
        amendments = build_amendments()
    except Exception as exc:
        _print_context(f"DECISION-SURFACE UNAVAILABLE: {exc}")
        return
    if hits:
        _print_context(render(rel, hits, amendments))


def _print_context(text: str) -> None:
    envelope = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}
    print(json.dumps(envelope))


def main() -> int:
    if "--self-test" in sys.argv:
        idx = build_index()
        print(f"index: {len(idx)} paths from {len({e['doc'] for v in idx.values() for e in v})} docs")
        for probe in (".claude/scripts/mnemos-pre-compact.sh", "skills/base/SKILL.md"):
            hits = lookup(probe, idx)
            print(f"\n{probe} -> {len(hits)} hit(s)")
            if hits:
                print(render(probe, hits, build_amendments()))
        return 0

    args = [a for a in sys.argv[1:] if a != "--hook"]
    target = args[0] if args else ""
    if not target:
        return 0
    if "--hook" in sys.argv:
        emit_hook(target)              # JSON envelope for the PreToolUse channel
        return 0
    # Plain-text mode: standalone/CLI use only. NOT the wired hook path.
    try:
        hits = lookup(relative(target), build_index())
        amendments = build_amendments()
    except Exception as exc:
        print(f"DECISION-SURFACE UNAVAILABLE: {exc}", file=sys.stderr)
        return 0
    if hits:
        print(render(relative(target), hits, amendments))
    return 0


if __name__ == "__main__":
    sys.exit(main())
