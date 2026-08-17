#!/usr/bin/env python3
"""Paths named in this repo's docs that are NOT this repo's to govern.

WHY THIS FILE EXISTS, AND WHY IT IS NOT `doccheck.PATH_ALLOWLIST`.

`decision_surface.py` indexes every backticked repo-shaped path in the ADRs and the
observatory, and treats it as a path those records GOVERN. A Switchyard evaluation that
backticks Switchyard's own `docs/architecture.md` therefore made an NVIDIA-proxy ADR fire
as a governing decision for any Tessera file that ever took that name (found 2026-08-15).

The first fix reused `doccheck.PATH_ALLOWLIST` on the reasoning that a human had already
written down which paths are not ours. **That reasoning was wrong and it broke something.**
`PATH_ALLOWLIST` means "not required to exist on disk", which is a different question with
several unrelated answers stacked in one set:

  - other repos' files            <- genuinely not ours
  - downstream projects' files    <- genuinely not ours
  - OUR OWN gitignored runtime state (`.mnemos`, `.claude/settings.local.json`, ...)
  - OUR OWN planned-but-unbuilt paths (`PLANNED_PATHS`)

The last two are Tessera's. Treating them as foreign silenced the decision surface on
`.claude/settings.local.json` — a live, 16KB, agent-editable file — dropping ADR-0009 and
the observatory entry "A Tessera skill silently shadowed a built-in command". A change made
to stop the hook firing WRONGLY instead stopped it firing AT ALL, on a real file. Planned
paths are the same error latent: an ADR that designs a file is exactly what you want to see
the day someone creates it.

So this set is defined by ONE question — "does this path belong to another repo or to a
downstream project?" — and nothing else. It is imported BY `doccheck` into
`PATH_ALLOWLIST`, so there is still a single definition of the data and
`referenced-paths-exist` is unchanged.

WHAT THIS FILE DELIBERATELY DOES **NOT** CONTAIN: the comparison. `decision_surface`
filters with its own inline prefix match and `doccheck`'s guard re-implements that match
independently, on purpose. The first version of that guard called the filter's own
predicate, so stubbing `_is_exempt` restored the whole defect (index 140 -> 148 keys) while
the check returned clean. **A guard must not share its predicate with the thing it guards**
— sharing the DATA is fine, sharing the COMPARISON makes the check an echo.

Stdlib-only and import-free by design: `decision_surface` imports this at module scope and
runs under a bare `python3` (possibly macOS 3.9) inside a PreToolUse hook, and it must work
in a scaffolded downstream where `doccheck.py` is not installed. `bin/tessera-new-project`
copies it alongside `decision_surface.py` and `decision_amendments.py` for that reason;
doccheck's `decision-surface-deps-ship-downstream` asserts it stays in that copy set.
"""

# EVERY ENTRY CARRIES ITS REASON, and a stale entry is a finding. Adopted 2026-08-17 from
# DeepSeek Harness (ADR-0024 §4), whose doc gates key their exemptions as {path: why} and fail
# when a key stops naming anything real. Bare sets are how `PATH_ALLOWLIST` came to mean two
# incompatible things at once and silenced the decision surface on a live file: with no reason
# beside each entry, the next reader infers the set's meaning from whichever comment they
# happen to read. A required sentence makes "another repo's" and "ours, absent" unmergeable.

# Other repos' files. The observatory *evaluates* Open GSD; it does not claim to contain it.
_OTHER_REPOS = {
    "bin/lib/state.cjs": "Open GSD's; the observatory evaluates GSD, it does not contain it",
    "bin/lib/capability-registry.cjs": "Open GSD's, same evaluation",
    "bin/lib/capability-loader.cjs": "Open GSD's, same evaluation",
    "docs/ARCHITECTURE.md": "Open GSD's architecture doc, cited by ADR-0001",
    ".claude/rules": "Open GSD's rules directory; Tessera has no such path",
    "docs/specification.mdx": "Braintrust's agent-behavior spec, cited by ADR-0020",
    "docs/client-implementation/adding-behaviors-support.mdx":
        "Braintrust's client-implementation guide, cited by ADR-0020",
}

# Claims about DOWNSTREAM projects, not about Tessera. Tessera is the framework: it consumes
# downstreams' FINDINGS.md (see bin/tessera-findings) and does not carry one, and
# _project_specs/session/ is the layout the base skill prescribes downstream.
_DOWNSTREAM = {
    "docs/FINDINGS.md": "downstream projects carry one; Tessera consumes them via bin/tessera-findings",
    "_project_specs/session": "the layout the base skill prescribes downstream, not here",
    "scripts/tessera-escalate": "a PATH-fallback bridge copy in conclave/howler; Tessera uses bin/",
}

FOREIGN_PATHS = frozenset(_OTHER_REPOS) | frozenset(_DOWNSTREAM)

# OURS, and deliberately not on disk. A DIFFERENT question from FOREIGN_PATHS, kept apart for
# the reason that whole file exists: conflating "not ours" with "not here" is the 2026-08-15
# defect. These stay INDEXED on purpose — an ADR that governed `bin/review` should fire the day
# someone recreates it, which is correct governance, not a phantom. What they must not be is
# *undeclared*, because then a genuinely foreign path written into an ADR is indistinguishable
# from one of these and nothing reports it (DOC_SKIP exempts ADRs from `referenced-paths-exist`).
ABSENT_TESSERA_PATHS = {
    "bin/kimi": "a real Tessera binary, deleted; ADR-0007/0014 record why",
    "bin/review": "a real Tessera binary, deleted by ADR-0014's review-stack prune",
    "bin/research": "a real Tessera binary, deleted; ADR-0014",
    # No trailing slash: `referenced-paths-exist` matches `token.rstrip("/") == p or
    # token.startswith(p + "/")`, so a stored slash matches neither form of the token.
    "skills/tessera-code-review": "the skill ADR-0014 cut",
    "docs/maggy-rfc.md": "written, never kept; ADR-0003 cites it as the upstream proposal",
    # scripts/tdd-loop-check.sh is deliberately NOT here: it never existed, which makes it
    # PLANNED_PATHS' question (unbuilt), not this set's (gone). Filing it here on day one was
    # the same one-set-two-questions conflation this module was written to end.
}

# Template shapes, never files: `.claude/scripts/X`, `docs/adr/NNNN-*.md`. Kept here rather
# than in doccheck so the two consumers agree on what a placeholder looks like; as with
# FOREIGN_PATHS, only the DATA is shared and each side applies it itself.
# `…` (U+2026) as well as `...`: ADR-0023's own explanation of this bug writes `docs/…`, and
# the ASCII-only pattern indexed it as a Tessera path — the trap, one layer down, again.
PLACEHOLDER_PATTERN = r"[{}]|/X$|NNNN|YYYY|TITLE|\.\.\.|…"
