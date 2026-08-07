# Conclave — Tessera dogfood findings

Runtime friction surfaced while working in Conclave. Framework-level fixes land in
`../tessera`, not here — these are staged for transfer and scanned by `tessera-findings`.

Contract: the downstream-findings contract in the Tessera framework
(`../tessera/docs/contracts/findings.md`). Each finding carries a `**Status:**` line
(`open` | `transferred:<ref>` | `rejected:<reason>`).

---

## F-001 — gate-scan re-flags already-adjudicated turns every Stop (no memory of dispositions)

**Status:** transferred:tessera@gate_disposition (2026-07-22)

**Where:** `scripts/gate/scan.py` (Stop hook) + the suggestion-gate log under `.tessera/logs/`.

**Friction:** the Stop-hook gate-scan counts gate-shaped turns in the transcript and diffs them
against the emitted-gate log, then makes me adjudicate the gap. It is designed to over-count (I
am the precision filter) — that part is fine. The friction is that it has **no memory of prior
adjudications**: turns I already dispositioned as *not a gate* (narration, a retry after a 4xx,
an investigation "reading the file") are **re-flagged on every subsequent Stop**, and re-listed
in the delta. Over a long session (this one hit it ~4×) the same non-gate turns —
`"Retrying with the form that's known to work:"`, `"Reading the full init:"` — kept reappearing,
so each Stop re-litigated closed decisions instead of only surfacing genuinely new gate-shaped
turns.

**Why it's framework-level:** the over-count is intentional and correct; the missing piece is
*disposition persistence*. A "not-a-gate" ruling on a specific transcript turn should be recorded
(the turn is content-addressable) and suppressed on later scans, so the delta only ever grows by
NEW gate-shaped turns since the last Stop.

**Suggested fix (lands in `../tessera`):** have `scan.py` write a small `skipped`/`not-a-gate`
ledger keyed by turn hash alongside the fired-gate log, and subtract BOTH the fired gates and the
skipped set from the detected set. The Stop prompt then asks only about turns adjudicated by
neither. Preserves the deliberate over-detection while making adjudication monotonic.

**Not logged here (not Tessera-framework):** AWS SSO tokens expiring mid-session (once caused a
401 crash mid-run) and the Claude Code sandbox blocking `git reset --hard` (forced manual
squash-merge cleanup) — both real friction this session, but harness/cloud behavior, not the
Tessera framework.

---

## F-002 — findings have a channel to the framework but none to a PEER project

**Status:** transferred:`../tessera/docs/observatory.md` → "Findings have a channel to the framework
but none to a PEER (conclave F-002, transferred 2026-08-07)"

> **Disposed 2026-08-07 in Tessera (Lorenzo signed off).** Landed as a **Watching** observatory entry
> carrying the sharp form of the gap, the four-part fix recorded unbuilt, the argument against a
> coordination database, and the two revisit triggers below. **Not `rejected:`** — the recommendation
> in this finding is *don't build yet*, which is a deferral, and `rejected:` would have hidden the
> trigger from the default backlog with nothing watching it. Triggers as written here: **a third peer
> pair**, or **the same fact found missing a second time**. Tessera's read on the near-miss: this
> reconciliation was a third *manual write*, not a trigger hit, and counting it as one would be
> manufacturing evidence for the mechanism.

**Where:** `../tessera/docs/contracts/findings.md`, `../tessera/bin/tessera-findings`, and the
SessionStart wiring in each downstream.

**Friction:** `FINDINGS.md` + `tessera-findings` is a working channel, but it is **hub-directed by
construction**. Every finding implicitly addresses the framework — there is no addressee field —
and only Tessera's SessionStart reads the backlog. So a fact one downstream measures that binds
*another downstream's* work has nowhere to go. It ends up in a coordination map
(`docs/contracts/three-project-cohesion.md`), which is read at coordination time, not at work time.

**What the evidence actually shows, and it is not what I first assumed.** Checking the conclave ↔
arbiter pair (both carry `.tessera/project.yml`, so both are downstreams):

- **Technical findings DID cross.** arbiter reviewed conclave twice; both defects are recorded in
  conclave at the site, credited by name and date, with the not-fixing rationale
  (`harness/run-t1t3.sh:139`, `:189`). That path works, because a finding about code has an obvious
  home — the code.
- **What did NOT cross is everything without a line number.** The usage rules that came with those
  reviews ("the finder is better at locating than at concluding — take the location, re-derive the
  consequence"; "`--ext ""` or the review is silently narrower than it claims") were absent from
  conclave until 2026-08-07, and they are the part needed *before* the next run, not after.
  Symmetrically, conclave's measurement that its local tier scores 0.073 recall on review — which
  bounds arbiter's cost work and the D3 seam — was absent from arbiter for ten days.

So the gap is narrower and sharper than "peers can't talk": **a finding that names a file finds its
own way home; a usage rule, a negative result, or a bound on someone else's design does not.**
Those are exactly the facts a coordination map is too slow to carry.

**Why it's framework-level:** the fix is one optional field and one hook line, not a new store. The
scanner already globs every `.tessera/` project, already parses `F-NNN` blocks and statuses, and
already emits `--json`. It is a distributed database with git as the store and SessionStart as the
notifier; it is missing an addressee, and the peers are missing the receiving end.

**Suggested fix (lands in `../tessera`):**
1. Optional `**To:** <project>` line in the finding shape. Absent = framework, so every existing
   finding stays valid and the contract change is backward-compatible.
2. `tessera-findings --to <project>` — a filter over parsing the scanner already does.
3. A SessionStart line in each downstream running `tessera-findings --to <self>`. **This is the
   load-bearing piece**; without it the change builds a mailbox nobody opens.
4. Add `acknowledged:<ref>` to the status vocabulary. A peer-directed finding's terminal state is
   not "transferred to the framework" — without it, peer findings can never close.

**Impact to weigh:** the contract says shape changes land there, and tess-dashboard consumes
`--json`. An addressee field has a downstream consumer.

**Explicitly NOT proposed: a coordination database.** It would move facts away from the code they
describe, need a service running at SessionStart, and could not be branched or reverted with the
change that motivated it. The evidence against it is in these repos already — arbiter's own docs
record a test count going stale twice and a commit trail running fifteen behind in a day, both
hand-maintained mirrors of facts a command could answer. Their rule was "prefer a command in the
doc over a number in the doc." A coordination DB is that failure class with a three-project blast
radius.

**When to fix:** not urgent — n is 2 projects and the manual writes are done. Worth doing if a
third peer pair appears, or if the same fact is found missing a second time.
