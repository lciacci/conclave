# Conclave — Tessera dogfood findings

Runtime friction surfaced while working in Conclave. Framework-level fixes land in
`../tessera`, not here — these are staged for transfer and scanned by `tessera-findings`.

Contract: the downstream-findings contract in the Tessera framework
(`../tessera/docs/contracts/findings.md`). Each finding carries a `**Status:**` line
(`open` | `transferred:<ref>` | `rejected:<reason>`).

---

## F-001 — gate-scan re-flags already-adjudicated turns every Stop (no memory of dispositions)

**Status:** open

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
