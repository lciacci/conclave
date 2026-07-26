#!/usr/bin/env python3
"""The decision -> amendment edge: which LATER records revisit a given ADR.

Split from decision_surface.py 2026-07-26 (one responsibility per file, 200-line limit).
decision_surface answers "which decisions govern this path?"; this answers "and has any of
them been revisited since?" — the question whose absence caused the failure below.

WHY IT EXISTS. A session read ADR-0008's verdict on a skill, acted on it, and missed a *later*
observatory entry deferring the whole question ("resolved there, not piecemeal"). The
decision-surface hook fired and showed ADR-0008 — it simply had no edge that could say
ADR-0008 had been amended, so a binding-looking verdict was nearly executed against a live
deferral. Every one of this repo's ADRs is referenced in the observatory (ADR-0008 fifteen
times), so amendment is the norm, not the exception.

DELIBERATELY NOT A JUDGEMENT. It reports that a later record *mentions* the ADR, never that it
qualifies it. Telling those apart needs reading, which is the reader's job. A regex on
`ADR-NNNN` is exact; a classifier for "does this amend?" would be the kind of proxy predicate
this repo has already retired three of.

Stdlib-only (CLAUDE.md's interpreter split) so a bare python3 can run it.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_AMENDMENTS = 4    # cap the REVISITED list; ADR-0008 alone has 15 referring records

# Tessera's own numbering only. The observatory cites Open GSD's ADR-1244 as external
# provenance; keying on it would mint an amendment bucket for a decision we do not own.
_ADR_REF = re.compile(r"ADR-(0\d{3})")
_ADR_TITLE = re.compile(r"^#\s*(ADR-\d+):\s*(.+?)\s*$")


# Tessera's own numbering only. The observatory cites Open GSD's ADR-1244 as external
# provenance; keying on it would mint an amendment bucket for a decision we do not own.
_ADR_REF = re.compile(r"ADR-(0\d{3})")


def build_amendments() -> dict[str, list[str]]:
    """`ADR-NNNN` -> titles of LATER records that discuss it. The missing edge.

    WHY THIS EXISTS (2026-07-26). The index above answers "which decisions govern this path?"
    It cannot answer "and has any of them been qualified since?" — and that is the question that
    bit. A session read ADR-0008's verdict on a skill, acted on it, and missed a *later*
    observatory entry deferring the whole question ("resolved there, not piecemeal"). The hook
    fired. It showed ADR-0008. It had no way to say ADR-0008 had been amended, so a binding-looking
    verdict was nearly executed against a live deferral.

    Every one of this repo's ADRs is referenced in the observatory (ADR-0008 fifteen times), so
    amendments are the norm, not the exception — and none of them reached the surfacing hook.

    DELIBERATELY NOT A JUDGEMENT. It reports that a later record *mentions* the ADR, never that
    it qualifies it — telling those apart needs reading, which is the reader's job. A regex on
    `ADR-NNNN` is exact; a classifier for "does this amend?" would be the proxy predicate this
    repo has already retired three of.
    """
    out: dict[str, list[str]] = {}
    obs = ROOT / "docs" / "observatory.md"
    if obs.exists():
        for chunk in re.split(r"^### ", obs.read_text(), flags=re.M)[1:]:
            title = chunk.splitlines()[0].strip()
            for num in set(_ADR_REF.findall(chunk)):
                out.setdefault(f"ADR-{num}", []).append(f"observatory: {title}")
    for adr in sorted((ROOT / "docs" / "adr").glob("0*.md")):
        body = adr.read_text()
        own = _ADR_TITLE.match(body.splitlines()[0]) if body else None
        if not own:
            continue
        for num in set(_ADR_REF.findall(body)):
            if f"ADR-{num}" == own.group(1):      # an ADR citing itself is not an amendment
                continue
            # Only a LATER ADR amends an earlier one; the reverse is just background.
            if adr.name[:4] > num:
                out.setdefault(f"ADR-{num}", []).append(f"{own.group(1)}: {own.group(2)}")
    return out


def render_amendments(adr_id: str, amendments: dict[str, list[str]]) -> list[str]:
    """Render lines for one ADR, or [] if nothing revisits it.

    The count sits NEXT TO the verdict rather than in a footer: an ADR reads as settled unless
    something says otherwise, and the failure was acting on a decision without knowing it had
    been revisited. A footer is skippable; this is not.
    """
    later = amendments.get(adr_id, [])
    if not later:
        return []
    out = [f"    \u26a0 REVISITED by {len(later)} later record(s) \u2014 read before acting:"]
    out += [f"        \u00b7 {t}" for t in later[:MAX_AMENDMENTS]]
    if len(later) > MAX_AMENDMENTS:
        out.append(f"        \u00b7 \u2026and {len(later) - MAX_AMENDMENTS} more")
    return out
