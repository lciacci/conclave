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

KNOWN LIMITS — stated here because a narrowing that lives only in the source is standing
pattern #12, and this module's output is what anyone actually reads:

1. **Untracked files are never claimed as reviewed.** `git diff` does not report them, and
   sweeping them in with `ls-files --others` cannot tell a new module the reviewer read from a
   scratch file in the tree. The two error directions are not symmetrical —
   `changed_since_review` SUBTRACTS the stamp's `uncommitted` list, so over-claiming silently
   drops a real finding while under-claiming only costs a false positive in a warn-only hook.
   Prefer noise. *(It subtracted the WHOLE `files` list until 2026-08-19, which cancelled
   `--head` for its dominant case — see `changed_since_review`.)*
2. **A stamp records the range a review was TOLD to cover, not the range it actually read.**
   On 2026-08-17 `/code-review high 0b27332` was invoked meaning "since that commit"; the skill
   read it as "that commit". Nothing here would catch that — the stamp would faithfully record
   the wrong scope. `--head` narrows the gap between what was reviewed and what is recorded; it
   does not close the gap between what was asked for and what was read.
3. **Stamping rides model recall.** Measured 2026-08-17: 3 stamps against 47 session logs. That
   is why `pre-push` is silent when no stamp exists — absence of a stamp is absence of
   evidence, and a branch that fires on the majority of pushes is one people learn to dismiss.

Contract: `docs/contracts/review-stamp.md`.

Stdlib-only: imported by a git hook that runs without the venv.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
EVENT = "review_stamped"
# HEAD, @, HEAD~2, HEAD^, @~1 — refs whose meaning is relative to where you are standing.
_HEAD_ANCHORED = re.compile(r"^(?:HEAD|@)(?:[~^].*)?$")
# Past this, an orphaned stamp is old news and reads as "no review on record", which this hook
# does not report. Matches `tessera-watch`'s DEGRADED_WINDOW_DAYS — an incident, not a state.
STALE_NOTE_DAYS = 7
# Sentinel: `None` is a MEANINGFUL value for `usable` ("there is no usable stamp"), so it cannot
# double as "not supplied". `pre-push` passes the value it already computed rather than making
# `latest_usable()` walk every log a second time — two git subprocesses per stamp row, per push,
# on an append-only log nothing prunes. (Review round 5.)
_UNSET = object()


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


def _git_lines(*args: str) -> "list[str] | None":
    """Lines of stdout, or None when git FAILED — the distinction `_git` throws away.

    `_git` returns "" for both an empty answer and a broken command, and a caller that tests
    truthiness silently treats "git could not tell me" as "there is nothing". That is the
    fail-open shape this repo keeps paying for, and it bit the base-intersection below within a
    day of it being written (review, 2026-08-19)."""
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                             text=True, timeout=15)
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return [line for line in out.stdout.strip().splitlines() if line]


def _git_ok(*args: str) -> bool:
    """True when the git command exits 0. `_git` returns stdout, which is empty for the
    predicates (`merge-base --is-ancestor`) whose whole answer is the exit code."""
    try:
        return subprocess.run(["git", *args], cwd=ROOT,
                              capture_output=True, timeout=15).returncode == 0
    except Exception:
        return False


def record(base: str = "origin/main", head: "str | None" = None) -> int:
    """Stamp the review's coverage: the commit it saw and the files in that range.

    `head` DEFAULTS TO HEAD AND MUST NOT BE FORCED TO IT. The documented workflow is "stamp
    after each review", but the real order is review -> fix -> commit -> stamp, and by then
    HEAD is the FIX. Hit live 2026-08-17: a review covering `cc5d2b3` was stamped as covering
    `5fb4dcb`, which no reviewer had seen — the anchor whose job is "what has no review looked
    at" recording the opposite, by default.

    Then three times in one session on 2026-08-19, which is what got this built: three review
    rounds on one branch, and not one of them could be stamped, because each round's fixes
    moved HEAD before there was anything to record. The reviews happened and the record of
    what they covered does not exist and cannot be reconstructed. `pre-push` on that branch
    reported its files as unreviewed against a stamp from the PREVIOUS session.

    An explicit head is a claim about the past, so it is verified: a ref that does not resolve
    is refused rather than stamped, for the same reason `_main` refuses an unresolvable base —
    `changed_since_review` will happily diff from whatever it is given, and a stamp against a
    phantom is worse than no stamp.

    `explicit` is recorded because the two cases are not equally trustworthy: a defaulted head
    means "whatever HEAD was when someone remembered", an explicit one means someone named the
    commit under review. A reader of the log can tell them apart; without the field they are
    identical rows.
    """
    # A HEAD-ANCHORED BASE IS NOT A CLAIM ABOUT THE PAST, IT IS A CLAIM ABOUT WHENEVER YOU LOOK.
    # `head` is resolved to a sha here precisely because a stamp records history; `base` is
    # deliberately kept as a REF so `origin/main` keeps tracking as others push. That is right
    # for symbolic refs and wrong for HEAD-relative ones, whose meaning moves with HEAD.
    # Probed 2026-08-19: `--head <R>` with `base=HEAD~1` re-resolves at push time to the FIX
    # commit, so the unreviewed fix is subtracted from the report; and `base=HEAD` makes the
    # outgoing range `HEAD...HEAD`, empty forever, so pre-push is permanently silent while
    # `staleness_note` says nothing is wrong. Refused rather than resolved, because freezing
    # `origin/main` to a sha would break the tracking the common case depends on.
    # (Review round 3.)
    if _HEAD_ANCHORED.match(base):
        print(f"base {base!r} is HEAD-anchored — refusing to stamp. It resolves to something "
              f"different at push time; name a branch, tag or sha.", file=sys.stderr)
        return 2
    # A REFLOG-RELATIVE BASE MOVES TOO. `@{-1}` and `HEAD@{1}` are re-resolved at push time
    # against the reflog and the last-checked-out branch, which is the same moving-base failure
    # `_HEAD_ANCHORED` exists to prevent. Round 4's comment CLAIMED these were covered and they
    # were not — the pattern has no `@{` case and the sha guard below only caught them when they
    # happened to point at HEAD. A false claim in a comment about a guard's coverage.
    # (Review round 5.)
    if "@{" in base:
        print(f"base {base!r} is reflog-relative — refusing to stamp. It resolves to something "
              f"different at push time; name a branch, tag or sha.", file=sys.stderr)
        return 2
    # THE BASE MUST NOT BE THE BRANCH YOU ARE ON — and the test is which REF it is, not which
    # commit it points at today. Round 4 compared `rev-parse base` to `rev-parse HEAD`, which
    # catches the real failure (`stamp.py main` on branch `main`: the outgoing range is
    # `main...HEAD`, empty forever) AND ALSO BLOCKS THE DOCUMENTED PRIMARY PATH: branch off a
    # synced `main`, review the uncommitted tree, run bare `stamp.py`, and `origin/main` equals
    # HEAD, so it refused to stamp at all. The premise only holds for refs that ADVANCE with the
    # local branch; `origin/main` stays put while HEAD moves. The fixture never caught it
    # because every test commits past `origin/main` before stamping. (Review round 5.)
    base_ref = _git("rev-parse", "--symbolic-full-name", base)
    if base_ref and base_ref == _git("rev-parse", "--symbolic-full-name", "HEAD"):
        print(f"base {base!r} is the branch you are on — refusing to stamp. The outgoing range "
              f"would be empty forever and this check would go silent.", file=sys.stderr)
        return 2
    if head is None:
        head = _git("rev-parse", "HEAD")
        explicit = False
    else:
        resolved = _git("rev-parse", "--verify", "--quiet", f"{head}^{{commit}}")
        if not resolved:
            print(f"head ref {head!r} does not resolve to a commit — refusing to stamp",
                  file=sys.stderr)
            return 2
        # AND IT MUST BE REACHABLE FROM HERE. A resolvable-but-unreachable sha — pasted from
        # another branch's review round, which the documented workflow ("copy the sha the review
        # saw") makes easy — is accepted by the check above, and then `changed_since_review`
        # hits its own ancestor guard and returns nothing. `latest()` keeps serving that stamp
        # by timestamp, so EVERY subsequent push is silent, and silent is indistinguishable from
        # "nothing changed since the review". Refused here for the same reason a phantom head is:
        # a stamp that cannot be reported against is worse than no stamp, and the failure is
        # quiet rather than loud. (Review, 2026-08-19.)
        if not _git_ok("merge-base", "--is-ancestor", resolved, "HEAD"):
            print(f"head {resolved[:12]} is not an ancestor of HEAD — refusing to stamp. "
                  f"A stamp off this line of history mutes pre-push silently.", file=sys.stderr)
            return 2
        head, explicit = resolved, True
    if not head:
        print("not a git repo, or no HEAD", file=sys.stderr)
        return 1
    # `_git_lines`, not `_git`: `git diff base...head` exits non-zero when the two have no merge
    # base, and `_git` returns "" — recording "git could not tell me" as "there is nothing".
    # That is the shape `_git_lines` was added for one function below, and this call site was
    # left on the fail-open helper in the same diff (review round 2).
    files = _git_lines("diff", "--name-only", f"{base}...{head}")
    if files is None:
        print(f"cannot diff {base}...{head[:12]} — no merge base? refusing to stamp",
              file=sys.stderr)
        return 2
    # THE WORKING TREE BELONGS TO *NOW*, NOT TO AN ARBITRARY PAST COMMIT. `record()` stores the
    # dirty diff because the review target is usually an uncommitted tree — true when stamping
    # HEAD, and false when stamping a commit from an hour ago, where today's uncommitted edits
    # were not in front of any reviewer. Including them would inflate the stamp's coverage,
    # which is the one direction that matters: `changed_since_review` SUBTRACTS the stamped
    # file list, so an over-claiming stamp silences real findings.
    # STAGED COUNTS AS UNCOMMITTED, and it did not until 2026-08-19. `git diff --name-only` is
    # UNSTAGED ONLY, so reviewing a tree that had been `git add`-ed — which is most of them,
    # since staging is how you decide what the review is about — recorded `had_uncommitted:
    # false` and an empty dirty list. Those files then reappeared in `changed_since_review` the
    # moment they were committed, as false positives, in the one branch whose whole job is
    # saying what no review has seen. Found by writing this module's first test; nothing else
    # would have, because the field it corrupts is only read one function away.
    # `checkpoint.py:_get_git_state` already read both, which is the usual shape of this repo's
    # defects: the correct version was next door.
    # AN EXPLICIT HEAD IS A CLAIM ABOUT A COMMIT, NOT ABOUT THE TREE SITTING ON TOP OF IT.
    #
    # The first version keyed on `head == rev-parse HEAD`, reasoning that naming HEAD should
    # behave like defaulting to it. That reads "the named commit is HEAD" as "the working tree
    # is what the reviewer saw", and the two come apart in exactly the documented workflow:
    # review -> fix -> commit -> stamp, where the fix is UNCOMMITTED for a window and stamping
    # during it is the natural moment. Probed 2026-08-19: review commit A, write and stage the
    # fix, `record(head=A)`, commit — the fix is recorded as `uncommitted`, subtracted by
    # `changed_since_review`, and pre-push says nothing. **The terminal fix stamped as
    # reviewed**, which is the over-claiming direction this module names as the dangerous one.
    # (Review round 2 on this branch.)
    #
    # So: the dirty tree belongs to the DEFAULT path only. A reviewer of an uncommitted tree
    # runs `stamp.py` with no flag — that is what the default is for — and nothing is lost.
    at_head = not explicit
    dirty = []
    blobs = {}
    if at_head:
        for args in (("diff", "--name-only"), ("diff", "--cached", "--name-only")):
            got = _git_lines(*args)
            if got is None:
                # THE THIRD SITE OF THE SAME SHAPE. `_git` cannot tell "the tree is clean" from
                # "git could not answer" (index.lock contention, a corrupt index), and the stamp
                # would be written claiming `uncommitted: []` about a tree nothing read — the
                # `restore_injected` shape this module cites as its founding lesson. Two other
                # call sites were moved off `_git` in rounds 2 and 3; this one was missed twice.
                print("cannot read the working tree — refusing to stamp", file=sys.stderr)
                return 2
            dirty += got
        dirty = sorted(set(dirty))
        # PIN EACH DIRTY FILE TO ITS CONTENT, or the subtraction becomes permanent. The dirty
        # set is subtracted from every later report so that committing the reviewed tree
        # unchanged is not a false positive — but round 1 fixed exactly this shape for `files`
        # and left it live here: probed 2026-08-19, a file dirty at stamp time was subtracted
        # after being re-edited TWICE, so F-004's terminal fix went unreported on the path the
        # contract calls primary. The blob hash is the discriminator: subtract while HEAD still
        # holds the content the reviewer saw, report the moment it changes. (Review round 3.)
        for f in dirty:
            h = _git("hash-object", "--", f)
            if h:
                blobs[f] = h
    event = {
        "type": EVENT,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session_id": os.environ.get("CLAUDE_CODE_SESSION_ID", "manual"),
        "source": "review-stamp",
        # `had_uncommitted` is OMITTED on an explicit stamp rather than written False. `dirty`
        # is never computed there, so False would be a claim about a working tree the code did
        # not look at — indistinguishable in the log from "the tree was clean". This module's
        # founding lesson is that `restore_injected` was a log line the hook wrote about itself;
        # a field asserting an unchecked fact is that, in miniature. (Review round 3.)
        "data": {"base": base, "head": head, "files": sorted(set(files + dirty)),
                 "uncommitted": dirty, "uncommitted_blobs": blobs,
                 "explicit_head": explicit,
                 **({} if explicit else {"had_uncommitted": bool(dirty)})},
    }
    with _log_path().open("a") as fh:
        fh.write(json.dumps(event) + "\n")
    how = "explicit" if explicit else "HEAD"
    print(f"review stamped: {head[:12]} ({how}) over "
          f"{len(event['data']['files'])} file(s) vs {base}")
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
            # `>=`, NOT `>`. `ts` has one-second resolution, so two stamps in the same second
            # resolved to the FIRST — and re-stamping is the documented remedy for a blind or
            # wrong stamp ("until you re-stamp"), which therefore silently no-op'd. Logs are
            # read in filename order and appended in time order, so the last writer wins.
            # (Review round 4.)
            if e.get("type") == EVENT and (best is None or e.get("ts", "") >= best.get("ts", "")):
                best = e
    return best


def latest_usable() -> "dict | None":
    """The newest stamp that can actually be reported against — an ancestor of HEAD.

    `latest()` returns the newest stamp ANYWHERE, and a stamp taken on another branch then
    shadows a perfectly good one. Reproduced: stamp `feature-a`, later stamp `feature-b`, return
    to `feature-a`, commit the terminal fix, push — the report is empty and the hook says it is
    blind, when a usable stamp for this line of history was sitting right there.

    The same defect produced the inverse noise: after a squash-merge, or on any unrelated branch,
    every push printed a blind note with no action available — the always-true signal this
    module's header rejects. Picking the newest ANCESTOR fixes both, and when there is none the
    fall-through is the deliberate `st is None` silence. (Review round 4.)
    """
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
            if e.get("type") != EVENT:
                continue
            if best is not None and e.get("ts", "") < best.get("ts", ""):
                continue
            head = e["data"]["head"]
            if _git("cat-file", "-t", head) and _git_ok("merge-base", "--is-ancestor",
                                                        head, "HEAD"):
                best = e
    return best


def changed_since_review(base: str = "origin/main") -> "tuple[dict | None, list[str]]":
    """(stamp, files in the outgoing range that changed after the stamp's HEAD)."""
    stamp = latest_usable()
    if stamp is None:
        return None, []
    head = stamp["data"]["head"]
    if not _git("cat-file", "-t", head):
        return stamp, []            # stamped commit is gone (rebase); nothing honest to say
    # A stamp whose commit is not an ANCESTOR of HEAD describes a different line of history —
    # stamp on a branch, switch, push, and `stamp_head..HEAD` enumerates everything since the
    # merge-base and calls it unreviewed. `cat-file` only catches a commit that is GONE.
    if not _git_ok("merge-base", "--is-ancestor", head, "HEAD"):
        return stamp, []
    # `_git_lines`: if THIS diff fails, `changed` was [] and pre-push printed nothing, while
    # `staleness_note` stayed silent because `cat-file` and `merge-base` both succeeded — "I am
    # blind" rendered as "you are clear". The same defect round 2 fixed at `record()`'s call
    # site, left live at the report's own. (Review round 3.)
    changed = _git_lines("diff", "--name-only", f"{head}..HEAD")
    if changed is None:
        # RAISE, DO NOT RETURN []. The first fix for this returned an empty list, which is the
        # defect restated: pre-push prints nothing for an empty list, so "I could not compute
        # the diff" still rendered as "you are clear". `.githooks/pre-push` wraps this call and
        # prints "review-anchor check unavailable (…)", which is the loud channel that already
        # exists. Caught by re-planting the fix and finding no test could tell the difference.
        raise RuntimeError(f"cannot diff {head[:12]}..HEAD — the report would be silent "
                           f"rather than empty")
    # `base` WAS AN UNUSED PARAMETER UNTIL 2026-08-19 (queue item 8c), and giving it a job is
    # what the explicit-head fix makes possible. `head..HEAD` is "everything since the review",
    # which over-reports the moment the stamped commit is behind the base ref — stamp, pull,
    # push, and every commit someone else already pushed is listed as unreviewed by you. The
    # question this hook asks is narrower: what is about to become PUBLIC that no review saw.
    # So intersect with the outgoing range. When the stamp is at or ahead of base this is a
    # no-op, which is the common case and why the defect was invisible.
    # `is not None`, NOT truthiness. An EMPTY outgoing range is a real answer — everything is
    # already public, so nothing is owed a review — and the correct report is nothing. Testing
    # truthiness skipped the filter in that case and fell back to the un-narrowed `head..HEAD`,
    # re-reporting commits that had already been pushed. Failure is the other branch and keeps
    # the wider report, because a filter that cannot be computed must not silently narrow.
    # THE STAMP'S OWN BASE WINS. `pre-push` always calls this with the default `origin/main`,
    # while `record()` stores the base actually used — and the contract documents
    # `stamp.py origin/release --head <sha>`. Reporting a release-line stamp against main's
    # outgoing range drops files whose HEAD content matches main's merge-base. The parameter
    # stays as the fallback for legacy rows and for direct callers. (Review round 2.)
    outgoing = _git_lines("diff", "--name-only",
                          f"{stamp['data'].get('base') or base}...HEAD")
    if outgoing is not None:
        changed = [f for f in changed if f in set(outgoing)]
    # SUBTRACT ONLY THE UNCOMMITTED FILES, and this distinction is the whole feature.
    #
    # The subtraction exists for the primary path: review an UNCOMMITTED tree, stamp, commit
    # those exact files, push. Those files are in `head..HEAD` afterwards and the review did see
    # them, so reporting them is a guaranteed false positive. (Review, 2026-08-17.)
    #
    # But subtracting the WHOLE stamped list cancels `--head` for its dominant case. A fix made
    # in response to review findings almost always re-edits a file the review looked at — that
    # IS the terminal fix this mechanism exists to catch — and every such file is in
    # `base...reviewed`, so the wide subtraction filtered it out and `pre-push` said nothing.
    # Measured 2026-08-19: review touches a.py, stamp --head A, fix re-edits a.py, report is
    # EMPTY. The suite missed it because its fixture added a NEW file, which is the one shape
    # that survives. (Review round 1 on this branch.)
    #
    # Files already COMMITTED at stamp time cannot reappear in `head..HEAD` unless they were
    # edited again, which is precisely what is worth reporting. Only the dirty set can produce
    # the false positive, so only the dirty set is subtracted.
    #
    # `files` is the fallback for stamps written before `uncommitted` existed: those are all
    # defaulted-head rows, and the wide subtraction is what they were recorded under.
    data = stamp["data"]
    if "uncommitted" in data:
        # BLOB-PINNED. A dirty file is subtracted only while HEAD still holds the content the
        # reviewer saw; once it changes again it is a new edit and the whole point is to report
        # it. Without the pin the subtraction was permanent — see `record()`.
        # LEGACY IS DECIDED BY THE FIELD'S PRESENCE, NOT BY A MISSING ENTRY. The first version
        # read `pinned is None` as "this stamp predates pinning" and subtracted unconditionally
        # — which is the over-claim the pin was added to stop, reachable whenever `hash-object`
        # cannot read a path. A DELETED file is the common case: `git rm a.py` puts `a.py` in
        # `diff --cached --name-only`, `hash-object` exits 128, no entry is written. Reproduced:
        # review a tree deleting a.py, stamp, commit, then re-add a.py with entirely unreviewed
        # content — never reported, on any push, forever. Non-ASCII paths do the same, since
        # `diff --name-only` emits them C-quoted.
        # Inside a pinned stamp, no pin means "we could not establish what was reviewed", and
        # the module's own rule for that is prefer the noise. (Review round 4.)
        pinned_stamp = "uncommitted_blobs" in data
        blobs = data.get("uncommitted_blobs") or {}
        seen = set()
        for f in data["uncommitted"]:
            if not pinned_stamp:                  # written before pinning existed
                seen.add(f)
            elif f in blobs and _git("rev-parse", f"HEAD:{f}") == blobs[f]:
                seen.add(f)
    else:
        seen = set(data.get("files", ()))         # rows from before `uncommitted` existed
    return stamp, [f for f in changed if f not in seen]


def staleness_note(stamp: "dict | None", usable: "dict | None" = _UNSET) -> "str | None":
    """Why the last stamp cannot be reported against, if it cannot. None when it can.

    `changed_since_review` returns an empty list for THREE different reasons — nothing changed,
    the stamped commit is gone, or HEAD has moved off its line of history — and `pre-push`
    printed nothing for all three. The record-time guard refuses a head that is not an ancestor
    AT STAMP TIME; nothing covered HEAD leaving that line afterwards, which an `--amend` or a
    rebase does routinely. Probed 2026-08-19: stamp, amend, commit something new, and the report
    is silent — indistinguishable from "nothing changed since the review", permanently, because
    `latest()` keeps serving that stamp by timestamp and stamping rides recall (limit 3).

    The record-time guard's own argument applies here verbatim: a stamp that cannot be reported
    against is worse than no stamp, and the failure is quiet rather than loud. So say it.

    Separate from `changed_since_review` rather than folded into it, because that function's
    contract is "what changed" and this one's is "can I answer at all". Merging them would make
    an empty list mean two things, which is the defect.
    """
    if stamp is None:
        return None
    # ONLY WHEN NOTHING USABLE EXISTS. `changed_since_review` now selects the newest ANCESTOR
    # stamp, so a stamp on another branch no longer shadows a good one — and this note must not
    # fire while a usable stamp is being reported against. Callers pass `latest()`; the note is
    # the difference between "a stamp exists" and "a stamp I can use exists". (Review round 4.)
    if (latest_usable() if usable is _UNSET else usable) is not None:
        return None
    # BOUNDED IN TIME, because an unbounded version is the signal this hook exists to avoid.
    # A stale stamp is never replaced on its own — stamping rides model recall (3 stamps / 47
    # sessions) — so once an --amend or rebase orphans one, "REVIEW ANCHOR IS BLIND" is true on
    # every push forever. That is P13's shape, which this module's own header and the no-stamp
    # branch were both written against, printed on the same channel as the useful report.
    # A RECENT stale stamp is news: you reviewed, something moved, re-stamp. An OLD one is
    # indistinguishable from never having stamped, and this hook is deliberately silent about
    # that. Probed 2026-08-19: three successive pushes after one amend, three banners.
    # (Review round 3.)
    try:
        age = datetime.now(timezone.utc) - datetime.strptime(
            stamp["ts"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        age = None
    if age is not None and age.days > STALE_NOTE_DAYS:
        return None
    head = stamp["data"]["head"]
    if not _git("cat-file", "-t", head):
        return (f"the last review stamp ({head[:12]}) names a commit that no longer exists — "
                f"rebased or amended away. This check is BLIND until you re-stamp.")
    if not _git_ok("merge-base", "--is-ancestor", head, "HEAD"):
        return (f"the last review stamp ({head[:12]}) is off this line of history — amended, "
                f"rebased, or taken on another branch. This check is BLIND until you re-stamp.")
    return None


def _main(argv: "list[str]") -> int:
    if any(a in ("-h", "--help") for a in argv):
        print("usage: stamp.py [BASE_REF] [--head SHA]   (default origin/main, HEAD)\n\n"
              "Records what a code review covered. Run AFTER a review completes.\n"
              "--head names the commit the review actually SAW. Use it whenever you have\n"
              "already committed the fixes: the normal order is review -> fix -> commit ->\n"
              "stamp, and by then HEAD is the fix, not what anyone reviewed.\n"
              "`.githooks/pre-push` reports what changed since.")
        return 0
    head = None
    rest = []
    it = iter(argv)
    for a in it:
        if a == "--head":
            head = next(it, None)
            if head is None:
                print("--head needs a value", file=sys.stderr)
                return 2
        elif a.startswith("--head="):
            head = a.split("=", 1)[1]
        elif a.startswith("-"):
            # A TYPO MUST NOT BECOME A BASE REF. The first parser silently DROPPED any
            # unrecognised `-` token, so `stamp.py --haed <sha>` dropped the flag and let the
            # sha fall through as the positional base: `review stamped: 1350ddb5 (HEAD) over 0
            # file(s) vs 1350ddb5...`, rc=0, the fix stamped as reviewed against a phantom base.
            # `_main` refuses an unresolvable base for exactly this reason and a mistyped flag
            # routed around it. New failure mode in this diff — before it there was no sha
            # argument to mistype. (Review round 2.)
            print(f"unknown option {a!r}", file=sys.stderr)
            return 2
        else:
            rest.append(a)
    args = rest
    if len(args) > 1:
        # `stamp.py origin/main <sha>` silently dropped the sha and stamped HEAD as reviewed —
        # the outcome `--head` exists to prevent, reached by forgetting the flag. The `--haed`
        # fix closed this for FLAGS only. (Review round 3.)
        print(f"unexpected extra argument {args[1]!r} — did you mean --head {args[1]}?",
              file=sys.stderr)
        return 2
    base = args[0] if args else "origin/main"
    if head is None and re.fullmatch(r"[0-9a-f]{7,40}", base):
        # NOT refused — a sha is a legitimate base. But `stamp.py <sha-the-review-saw>` reads as
        # "base = that sha, head = HEAD", i.e. the fix stamped as reviewed, and the two
        # intentions are indistinguishable from the argv alone. Loud, then proceed.
        print(f"note: {base!r} is being used as the BASE. If it is the commit the review saw, "
              f"you want --head {base}.", file=sys.stderr)
    # A base that does not resolve produced a stamp claiming coverage vs `--help` on the first
    # run. A stamp is a claim about SCOPE; a claim against a ref that does not exist is worse
    # than no claim, because `changed_since_review` will happily diff from it.
    if not _git("rev-parse", "--verify", "--quiet", base):
        print(f"base ref {base!r} does not resolve — refusing to stamp", file=sys.stderr)
        return 2
    return record(base, head)


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
