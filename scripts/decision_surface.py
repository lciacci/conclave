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
# Module scope, like decision_amendments above, NOT a defensive try/except. An earlier
# version imported doccheck lazily-and-defensively so a failure degraded to "exempt
# nothing" — but doccheck is not shipped downstream, so that degradation was permanent and
# invisible in every scaffolded project, and the arm meant to catch it could never fire in
# the only process that runs the check. A hard import states the dependency honestly;
# bin/tessera-new-project copies this file with its deps, and doccheck's
# `decision-surface-deps-ship-downstream` asserts repo_paths stays in that copy set.
from repo_paths import FOREIGN_PATHS, PLACEHOLDER_PATTERN

ROOT = Path(__file__).resolve().parent.parent
MAX_DOCS = 3

_PLACEHOLDER = re.compile(PLACEHOLDER_PATTERN)

# Backtick-quoted repo paths, anywhere in the body — NOT just the References section.
# ADR-0004 names templates/tessera/settings.base.json in its prose and in References;
# most of the signal in the observatory is in prose only. Restricting to References
# would have missed the very case that motivated this file.
_PATH = re.compile(r"`((?:bin|scripts|templates|docs|hooks|\.claude|_project_specs)/[A-Za-z0-9._/-]+)`")
_ADR_TITLE = re.compile(r"^#\s*(ADR-\d+):\s*(.+?)\s*$")
_STATUS = re.compile(r"^-\s*\*\*Status:\*\*\s*(.+?)\s*$", re.M)
_EXECUTED = re.compile(r"^-\s*\*\*Executed:\*\*\s*(.+?)\s*$", re.M)

# Leading punctuation stripped off a "partially — <detail>" value. A NAMED CONSTANT so the
# em dash never sits inside an f-string EXPRESSION — a backslash there is 3.12+ syntax, and
# this module is imported by doccheck, which must parse on /usr/bin/python3 (3.9.6). In a
# plain string literal the character itself is fine on every version; it is only the
# f-string expression slot that is version-sensitive.
_EM_DASH_STRIP = " —-"


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
        # The strip set is hoisted out of the f-string ON PURPOSE: a backslash inside an
        # f-string EXPRESSION is 3.12+, and this module is imported by doccheck, which
        # `safety-scripts-run-on-system-python` requires to run on /usr/bin/python3 (3.9.6
        # here). The old inline `\u2014` made decision_surface unparseable there, so
        # doccheck was RED on the exact interpreter that check exists to protect.
        detail = value[len("partially"):].lstrip(_EM_DASH_STRIP)
        return f"\u23f3 PARTIALLY EXECUTED \u2014 {detail}"
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


def _is_exempt(path: str) -> bool:
    """True when a backticked token belongs to ANOTHER repo or to a downstream project.

    Reads `repo_paths.FOREIGN_PATHS`, which answers exactly that one question. It must NOT
    be widened back to `doccheck.PATH_ALLOWLIST`: that set means "not required to exist on
    disk", which also covers Tessera's OWN gitignored runtime state and its own planned
    paths. Using it here silenced this hook on `.claude/settings.local.json` — a live 16KB
    file — dropping ADR-0009 and an observatory entry. See repo_paths' module docstring.

    THE COMPARISON BELOW IS DUPLICATED IN `doccheck.check_decision_surface_honors_path_
    exemptions` ON PURPOSE. That guard must not call this function: the first version did,
    so stubbing this predicate restored the entire defect (index 140 -> 148 keys) while the
    check reported clean. Data is shared; the comparison is not.
    """
    if _PLACEHOLDER.search(path):
        return True
    bare = path.rstrip("/")
    return any(bare == p or path.startswith(p + "/") for p in FOREIGN_PATHS)


def build_index() -> dict[str, list[dict]]:
    """path-prefix -> [{doc, title, gloss, kind}]. Raises if the record is unreadable.

    Paths belonging to another repo or to a downstream project are skipped — see
    `_is_exempt`. A foreign repo's `docs/architecture.md` must not make an evaluation of
    that repo fire as a governing decision the day this repo gains a file by that name.

    Tessera's OWN paths are never skipped, including ones not on disk: gitignored runtime
    state and planned-but-unbuilt files are exactly where a governing decision should
    surface the moment someone creates them.
    """
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
            if _is_exempt(path):
                continue
            index.setdefault(path, []).append(entry)

    obs = ROOT / "docs" / "observatory.md"
    if obs.exists():
        # Split on ### so each entry's paths attach to that entry, not the whole file.
        for chunk in re.split(r"^### ", obs.read_text(), flags=re.M)[1:]:
            title = chunk.splitlines()[0].strip()
            entry = {"doc": "docs/observatory.md", "title": title,
                     "gloss": "", "kind": "observatory", "sort": "z"}
            for path in set(_PATH.findall(chunk)):
                if _is_exempt(path):
                    continue
                index.setdefault(path, []).append(entry)
    return index


def _matches(target: str, index: dict[str, list[dict]]) -> list[dict]:
    """Every record governing `target`, by prefix, sorted, UNTRUNCATED.

    Prefix matching is the load-bearing part — an ADR naming .claude/scripts/ covers every
    script under it, which is exactly the generalisation that was missed. It is also why the
    count of affected keys is 46 and not 14: measuring direct attachment only, and ignoring
    what prefix matching pulls in, understates the truncation ~3× (2026-08-17).
    """
    hits, seen = [], set()
    for path, entries in index.items():
        if target == path or target.startswith(path.rstrip("/") + "/") or path.startswith(target + "/"):
            for e in entries:
                key = (e["doc"], e["title"])
                if key not in seen:
                    seen.add(key)
                    hits.append(e)
    hits.sort(key=lambda e: (e["kind"] != "adr", e["sort"]), reverse=False)
    return hits


def lookup(target: str, index: dict[str, list[dict]]) -> list[dict]:
    """The records shown for `target` — at most MAX_DOCS of them."""
    return _matches(target, index)[:MAX_DOCS]


def lookup_split(target: str, index: dict[str, list[dict]]) -> tuple[list[dict], list[dict]]:
    """`lookup()`'s result plus what it discarded, as (shown, cut).

    A separate entry point rather than a changed `lookup()` signature: `lookup` is the
    established read used by callers outside this module, and widening it to a tuple would be a
    silent breaking change to a hook that must never crash. `render_truncation` documents why
    the cut is worth reporting at all.
    """
    all_hits = _matches(target, index)
    return all_hits[:MAX_DOCS], all_hits[MAX_DOCS:]


def render_truncation(cut: list[dict]) -> list[str]:
    """Name what MAX_DOCS dropped. Additive-only, deliberately.

    Until 2026-08-17 the render ended at `Read before editing:` and said nothing about the
    records it had discarded — on 46 of 146 index keys it cuts something, and what it cuts is
    the NEWEST, because the sort is ADR-filename ascending. ADR-0022 ("a crashed doccheck check
    blocks the commit") was invisible on `scripts/doccheck.py`; ADR-0015, which created the P3
    predicate, was invisible on `bin/tessera-watch`. A true report, silently narrowed, with the
    narrowing absent from the output — standing pattern #12, inside the hook built to defeat
    silent failure.

    WHY A NOTICE AND NOT A BIGGER CAP OR A DIFFERENT SORT. Raising MAX_DOCS trades a silent drop
    for prefix dilution, which is unmeasured (ADR-0021). Re-sorting needs a notion of specificity
    that `lookup()` does not compute — it matches by prefix, so a record naming `scripts/` and one
    naming the exact file are indistinguishable at sort time. Both are real changes to what the
    hook SHOWS, and a change made to stop this hook firing wrongly already once stopped it firing
    on something real (the 2026-08-15 PATH_ALLOWLIST defect). Adding a line cannot suppress a live
    record, so it is the half that carries no such risk.

    ADR ids are listed in full and the rest are counted, so this line never truncates in silence
    the way the thing it reports on did.
    """
    if not cut:
        return []
    adrs = sorted(h["title"].split()[0] for h in cut if h["kind"] == "adr")
    others = len(cut) - len(adrs)
    parts = ", ".join(adrs) if adrs else ""
    if others:
        parts += f"{' + ' if parts else ''}{others} observatory entr{'y' if others == 1 else 'ies'}"
    return [f"  ⚠ {len(cut)} more record(s) NOT shown (MAX_DOCS={MAX_DOCS}, oldest-ADR-first): {parts}"]


def render(target: str, hits: list[dict], amendments: dict[str, list[str]] | None = None,
           cut: list[dict] | None = None) -> str:
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
    out += render_truncation(cut or [])
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
        hits, cut = lookup_split(rel, build_index())
        amendments = build_amendments()
    except Exception as exc:
        _print_context(f"DECISION-SURFACE UNAVAILABLE: {exc}")
        return
    if hits:
        _print_context(render(rel, hits, amendments, cut))


def _print_context(text: str) -> None:
    envelope = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": text}}
    print(json.dumps(envelope))


def main() -> int:
    if "--self-test" in sys.argv:
        idx = build_index()
        print(f"index: {len(idx)} paths from {len({e['doc'] for v in idx.values() for e in v})} docs")
        for probe in (".claude/scripts/mnemos-pre-compact.sh", "skills/base/SKILL.md"):
            hits, cut = lookup_split(probe, idx)
            print(f"\n{probe} -> {len(hits)} shown, {len(cut)} cut")
            if hits:
                print(render(probe, hits, build_amendments(), cut))
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
        hits, cut = lookup_split(relative(target), build_index())
        amendments = build_amendments()
    except Exception as exc:
        print(f"DECISION-SURFACE UNAVAILABLE: {exc}", file=sys.stderr)
        return 0
    if hits:
        print(render(relative(target), hits, amendments, cut))
    return 0


if __name__ == "__main__":
    sys.exit(main())
