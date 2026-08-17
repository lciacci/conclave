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
  Symmetrically, conclave's measurement that its local tier scored 0.073 recall on review (**a
  figure since RETRACTED — see F-003 below**) — which bounds arbiter's cost work and the D3 seam —
  was absent from arbiter for ten days.

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

> **🔔 TRIGGER HIT 2026-08-14 — see F-003 below.** The second of the two revisit triggers
> ("the same fact is found missing a second time") has now fired, in its worse form: the fact did
> not go missing, it went *stale in place* across both peers simultaneously. Tessera's disposition
> note said counting the 2026-08-07 reconciliation as a trigger would be manufacturing evidence.
> This is not that — it is an organic, dated, three-repo instance.

---

## F-003 — a retracted number stays live in every peer that copied it

**Status:** transferred:`../tessera/docs/observatory.md` → F-002 revisit-trigger block (2026-08-14)

> **All six peer citations CORRECTED 2026-08-14** — `../arbiter@c533e8d` (INTEGRATION.md ×2,
> NEXT_SESSION.md, STATE.md ×2) and `../tessera@bdb67fe` (three-project-cohesion.md ×2,
> design-principles.md ×2, observatory.md ×2). Scope held deliberately: the **retracted fact** and
> the justification resting on it were corrected; **guard (b)'s verdict was not touched**, because
> it does not change — it gets stronger. No ADR proposed.
>
> **Two things the propagation itself taught, both worth more than the corrections:**
> 1. **The retraction changed a peer's ANSWER, not just its number.** arbiter's docs used 0.073 to
>    close the cheap-finder question — *"conclave measured it and it is dead."* At 0.309 recall with
>    **half** claude's false positives, for $0, feeding a triage stage arbiter already runs, that
>    question is re-opened. Had this not been pushed, arbiter would have kept declining an option on
>    evidence that no longer existed. That is the concrete cost of the F-002 gap, finally priced.
> 2. **The correction pass fixed the PEER's copy of a sentence and missed conclave's own.**
>    F-002's narrative above cited 0.073 in the present tense. `../tessera/docs/observatory.md`
>    carries that same sentence and it *was* corrected; the conclave original was not, and sat 45
>    lines above this closure block claiming the propagation was complete. Caught by code review,
>    not by the sweep. **F-003's failure mode reproduced inside the commit that closes F-003** —
>    which is the strongest argument yet that the `--grep` sweep below should exist, since the sweep
>    was run *by hand* and by someone who had just written the rule.
> 3. **Tessera's own doccheck caught an error in the correction.** The first commit was BLOCKED —
>    `referenced-paths-exist` flagged that a conclave-relative path (`docs/S2-scoping.md`) had been
>    written into a Tessera-hosted doc where it does not resolve. A cross-repo write is exactly
>    where that class of mistake happens, and the receiving repo's gate is what caught it.

**Where:** `../arbiter/docs/{INTEGRATION.md,NEXT_SESSION.md,STATE.md}`,
`../tessera/docs/{design-principles.md,observatory.md,contracts/three-project-cohesion.md}`.

**Friction:** conclave measured `qwen3-coder:30b` at **0.073 recall / 0-of-8 criticals** on
adversarial review and published it as the local tier's capability. On 2026-08-14 conclave retracted
it: at a matched token budget the same model scores 0.127, and a current same-footprint model
(`muse-glimmer:30b`) scores **0.309 / 5-of-8**. The "~7× weaker" characterisation is dead.

The retraction landed in conclave in one pass — 8 files, because a `grep` finds them. It did **not**
land in the six peer locations, and conclave cannot land it there: `three-project-cohesion.md` is
the co-owned canonical (rewriting it is ADR-level), and arbiter's docs are arbiter's. The number is
load-bearing in both: arbiter cites it as "a local tier that can review is dead", and Tessera cites
it in the D3 seam and in principle #12's evidence chain.

**Why it's framework-level, and why it is F-002's sharper twin.** F-002 established that a finding
naming a file finds its own way home while a usage rule does not. This adds a third class and it is
the worst of the three: **a NUMBER that was copied**. It propagates *better* than a usage rule
(everyone quotes it), which means it goes stale in more places, and unlike a file-anchored finding
there is no local artifact whose existence contradicts it. Nothing in any repo goes red when an
upstream figure is retracted.

**Suggested fix (lands in `../tessera`):** F-002's proposed `**To:** <project>` addressee plus
`acknowledged:<ref>` is necessary but not sufficient here — a retraction needs *push*, not a
mailbox. Minimum viable: when a finding's status becomes `retracted:`, `tessera-findings` should
list every downstream that has cited it. That requires citations to be marked, which they are not.
The cheap 80% version, requiring no schema change: **a `tessera-findings --grep <phrase>` that
sweeps all `.tessera/` projects for a literal string**, so "who else quotes 0.073?" is one command
instead of the ad-hoc `grep -rn` across sibling repos that found these six.

**Impact to weigh:** the ad-hoc grep worked, took seconds, and needed no framework change. The
honest counter-argument to building anything is that `grep` already is the tool and the real failure
was nobody running it. Against that: this repo's own memory carries a
[[retraction-propagation]] rule that says to run exactly that grep, and it still took four months
and a fresh measurement to catch. A rule that is documented and skipped is a candidate for
automation.

**When to fix:** the corrections are the urgent part and they are manual either way. Build nothing
until a third instance appears — but record the count, because this is now two.

---

## F-004 — the review gate covers the draft and never the fix

**Status:** transferred:`../tessera` @ 117ebb1 + bdc70ad (2026-08-17) — the rule, and a narrower
mechanism than this finding proposed. Fix (1) DECLINED, twice, independently. Fix (3) rebuilt after
its proposed design proved a false green. **The recurrence trigger this finding set — "a different
session on different work" — is what fired.**
**Surfaced:** 2026-08-15

> **Disposition, 2026-08-17, written from a Tessera session (precedent: F-002 `cf02f10`).**
>
> **The trigger fired.** Tessera hit this on 2026-08-17: three review rounds on one change, and the
> terminal round's fixes were pushed unreviewed (`0440193`). Different session, different repo,
> different work — the condition `917936c` set.
>
> **Fix (1) is not built, and this repo's argument is the one that held.** `917936c` declined it on
> the ground that *F-003's standard for automating is a rule documented and SKIPPED, and you cannot
> skip a rule nobody wrote*. Tessera reached the same verdict independently by a weaker route (a
> Stop hook has no fixed point — "your latest edits are unreviewed" is true whenever you stop, and
> every re-review leaves its own fixes unreviewed). Both stand; the first is stronger.
>
> **But the rule written here on 2026-08-15 never reached Tessera** — not its CLAUDE.md, not a
> skill, not a contract. So Tessera hit the pattern, re-derived the verdict, and *built a mechanism
> a day after this repo decided not to*. **That is F-002's subject scoring the framework**: the
> peer channel this file opened for findings does not carry DECISIONS, and the two repos agreeing
> was luck. Recorded in Tessera's rule text rather than smoothed over. It is the sharpest thing this
> finding produced and it is not what the finding was about.
>
> **What Tessera measured that narrows the claim.** "The edits made in response to a review are the
> only part nothing has looked at" is false in the general form: a review targeting
> `origin/main..HEAD` re-covers the previous round's fixes for free. Rounds 1 and 2 were reviewed;
> only the terminal one escaped. **The push is what makes the hole permanent, not the fixing** — so
> the boundary is pre-push, not Stop.
>
> **Shipped:** the rule (`bdc70ad`, both repos' reasoning); `scripts/review/stamp.py` +
> `.githooks/pre-push`, warn-only, reporting the set difference since the last recorded review — a
> set difference terminates where a recursion does not. Its own first version had a second branch
> that fired when no review was recorded; measured at 3 stamps against 47 sessions, that is P13's
> always-true shape, and it was cut (`55cdab0`).
>
> **Fix (3) shipped and its proposed design was replaced.** A `deployed:` date compared against the
> file's last content commit **was built first and was a false green** — `docs/promo/index.html`
> took four commits on 2026-08-17 and a same-day date cannot distinguish them, so the design failed
> on the instance that motivated it. Replaced with a content hash that excludes the marker from its
> own input, which is what stops a marker-only edit satisfying it. Limit stated in the artifact: it
> records a human's CLAIM of an upload, with no second party available to check it.
>
> **Fix (2) untouched** — `/code-review --fix` re-reviewing its own applied diff is upstream of
> both repos.

The pre-commit discipline is "run `/code-review` before committing". As actually practised the
sequence is **review → apply the findings → commit**, which means **the edits made in response to
the review are the only part of the change that no review ever sees.** They are also the
highest-risk part: they were written under time pressure, in the areas already known to be
delicate, by the same author who got them wrong the first time.

**Concretely, this session.** Round 1 flagged a HIGH in `docs/INTEGRATION.md` (a claim that conclave's
proxy harness does per-request routing when it does launch-time interposition). I rewrote the
paragraph, committed, and pushed. The rewrite contained **a second over-claim** — wrong on duration,
wrong on chronological order, and contradicted by this repo's own notes — which a *later* review
caught only because the owner asked whether anything had gone unreviewed. **The instrument did not
notice the gap; a human did.** By then the claim had reached two repos and a deployed page, so the
fix cost a re-deploy rather than a commit.

> **Sourcing that last clause, because a review flagged it as unsupported and it is load-bearing.**
> The wrong chronology was live on `../tessera/docs/promo/index.html` only between `bc69aeb` and
> `7bfb108` — about ten minutes. It reached the deployed copy because the owner uploaded both HTML
> pages inside that window and said so in-session. **That evidence is a conversation, not an
> artifact**, so nothing in either repo records it and the claim is unciteable by anyone reading
> later. Which is its own small instance of the problem: the deploy state of a hand-uploaded page
> lives only in whoever remembers doing it.

**Why it's framework-level.** Nothing here is specific to conclave's subject matter. Any project
running the gate has the same shape, and the failure is silent by construction: after a review, the
tree looks *more* reviewed than before, so the natural next action is to commit. The gate produces
a false sense of coverage exactly proportional to how many findings it produced.

**It compounds with manually-deployed artifacts.** Tessera's `doccheck` caught a *missing* ADR row on
`docs/promo/index.html` and blocked on it — good. Nothing catches a row that is *present and wrong*,
and the page is uploaded by hand, so a bad claim in it outlives the commit that fixed it. Conclave
has the same class of artifact (`docs/index.html`) and the same hole. This is F-003's shape one step
downstream: not "a retraction fails to reach peers", but "a correction reaches the repo and not the
deployed copy".

**Suggested fix, cheapest first.** Fix (1) is the exception to this file's usual "lands in
`../tessera`" rule: `scripts/gate/scan.py` exists in **both** repos (conclave's own copy dates to
`e5eb582`, 2026-07-11), so a detector could be prototyped here as dogfood before the contract moves.
Fixes (2) and (3) are framework-side.
1. **A Stop-hook check, in the shape `gate/scan.py` already has** — verified, not recalled: it walks
   the transcript, content-addresses candidate turns, diffs them against the log, persists
   dispositions (F-001's fix) and makes you adjudicate the gap. If the transcript shows a
   `/code-review` invocation followed by file edits followed by a commit, with no second review
   between, make it adjudicable — the same "you did a gate-shaped thing and did not log it" pattern
   that already backstops the suggestion-gate. This is the 80% fix and needs no new instrument.
2. `/code-review --fix` already applies findings itself; if it re-reviewed its own applied diff, the
   most common path would close without any hook.
3. For the deploy hole: a `deployed:` marker in the page plus a doccheck rule that the marker's date
   is not older than the file's last content commit. That turns "needs re-upload" from a thing a
   session has to remember into a thing that fails loud.

**When to fix:** n=1, so by F-003's own standard, build nothing yet.

> **Retracted from this section, 2026-08-15, same day it was written.** It originally argued that
> "unlike F-001–F-003 this one has a known cost already paid rather than a hypothetical one." That
> is false and the counter-evidence is in this same file: F-003 prices its cost explicitly ("arbiter
> would have kept declining an option on evidence that no longer existed … finally priced"), and
> F-001 records non-gate turns re-litigated ~4× in one session. **A paid cost is not what makes this
> one different**, so the "build despite n=1" argument that leaned on it is withdrawn. What actually
> distinguishes F-004 is narrower and worth less: fix (1) reuses an existing hook shape rather than
> inventing a mechanism.

The honest reason to record it now is the count, not the severity.

**Honest counter-argument:** the discipline "re-run the review after applying fixes" requires no
tooling at all, and n=1 is a thin basis for a hook. The case against that is the same as F-003's —
this repo's memory already carried "run the adversarial pass before propagating" and the pass *ran*;
the fix still shipped unreviewed. A rule that is documented, followed, and still leaves the hole is a
better automation candidate than one that was merely skipped. See [[overclaim-reflex]] (4th instance,
2026-08-15) and [[code-review-before-commit]].
