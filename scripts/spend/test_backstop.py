"""Tests for the spend-denial backstop.

The predicate is the whole design: a denial must end in a grant or a packet. The two
"correct" paths must stay quiet, or the hook is noise and gets ripped out — and then it
protects nothing.
"""
import json

import pytest

import backstop
from backstop import _escalated, _events, main, undispositioned

SID = "sess-abc"


def ev(kind, ts, **data):
    return {"type": kind, "ts": ts, "session_id": SID, "data": data}


DENIED = ev("spend_denied", "2026-07-12T12:00:00Z", command="terraform apply", reason="no auth")


# ── the three dispositions ────────────────────────────────────────────────────

def test_denial_with_nothing_after_it_fires():
    """The bug: the agent was denied, did something else, and the block vanished."""
    assert undispositioned([DENIED], escalated=False) == [DENIED]


def test_denial_followed_by_a_grant_is_quiet():
    """The SUPERVISED path. A human saw the denial and granted an envelope. Correct — and if
    this fired, the hook would nag on every ordinary authorized boot and get disabled."""
    events = [DENIED, ev("spend_authorized", "2026-07-12T12:01:00Z", usd=20)]
    assert undispositioned(events, escalated=False) == []


def test_denial_followed_by_an_escalation_is_quiet():
    """The UNSUPERVISED path. The agent raised a packet and stopped. Correct."""
    assert undispositioned([DENIED], escalated=True) == []


# ── ordering is load-bearing ──────────────────────────────────────────────────

def test_a_grant_BEFORE_the_denial_does_not_count():
    """An envelope that expired is what CAUSED the denial. Counting it as the disposition
    would silence the hook on exactly the case it exists for: an agent that blew past an
    expired envelope and kept going."""
    events = [ev("spend_authorized", "2026-07-12T08:00:00Z", usd=20), DENIED]
    assert undispositioned(events, escalated=False) == [DENIED]


def test_grant_then_denial_then_grant_is_quiet():
    events = [
        ev("spend_authorized", "2026-07-12T08:00:00Z", usd=20),
        DENIED,
        ev("spend_authorized", "2026-07-12T12:05:00Z", usd=40),
    ]
    assert undispositioned(events, escalated=False) == []


def test_second_denial_after_a_grant_still_fires():
    """Denied, granted, then denied again (envelope too small) and dropped. The last denial
    is the one that must be answered for."""
    events = [
        DENIED,
        ev("spend_authorized", "2026-07-12T12:01:00Z", usd=5),
        ev("spend_denied", "2026-07-12T13:00:00Z", command="terraform apply", reason="expired"),
    ]
    assert len(undispositioned(events, escalated=False)) == 2


# ── quiet when there is nothing to say ────────────────────────────────────────

def test_no_denials_is_quiet():
    assert undispositioned([ev("spend_authorized", "2026-07-12T12:00:00Z")], escalated=False) == []


def test_empty_session_is_quiet():
    assert undispositioned([], escalated=False) == []


def test_unrelated_events_are_ignored():
    other = {"type": "suggestion_gate", "ts": "2026-07-12T12:00:00Z", "data": {}}
    assert undispositioned([other], escalated=False) == []


# ── reading the log ───────────────────────────────────────────────────────────

def test_events_survives_a_torn_line(tmp_path):
    log = tmp_path / f"{SID}.jsonl"
    log.write_text(json.dumps(DENIED) + "\n{ not json\n" + json.dumps(DENIED) + "\n")
    assert len(_events(SID, tmp_path)) == 2


def test_events_on_missing_log_is_empty(tmp_path):
    assert _events("nope", tmp_path) == []


def test_escalated_matches_on_session_id(tmp_path):
    (tmp_path / "esc-1.json").write_text(json.dumps(
        {"id": "esc-1", "session_id": SID, "category": "spend_unauthorized"}))
    assert _escalated(SID, tmp_path) is True
    assert _escalated("other-session", tmp_path) is False


def test_escalated_survives_a_corrupt_packet(tmp_path):
    (tmp_path / "bad.json").write_text("{ not json")
    (tmp_path / "esc-1.json").write_text(json.dumps(
        {"id": "esc-1", "session_id": SID, "category": "spend_unauthorized"}))
    assert _escalated(SID, tmp_path) is True


# ── the hook contract ─────────────────────────────────────────────────────────

def test_hook_exits_2_and_names_the_command(monkeypatch, capsys):
    monkeypatch.setattr("backstop._events", lambda s, root=None: [DENIED])
    monkeypatch.setattr("backstop._escalated", lambda s, root=None: False)
    monkeypatch.setattr("backstop._bump_fires", lambda s: 1)
    assert main(["backstop.py", SID]) == 2
    err = capsys.readouterr().err
    assert "terraform apply" in err
    assert "tessera-escalate raise" in err
    assert "tessera-authorize grant" in err
    assert "FALSE POSITIVE" in err   # the honest out, so the hook doesn't force a bogus packet


def test_hook_exits_0_when_dispositioned(monkeypatch):
    monkeypatch.setattr("backstop._events", lambda s, root=None: [DENIED])
    monkeypatch.setattr("backstop._escalated", lambda s, root=None: True)
    assert main(["backstop.py", SID]) == 0


def test_hook_caps_its_fires(monkeypatch):
    """A backstop that can wedge a session gets ripped out, and then it protects nothing."""
    monkeypatch.setattr("backstop._events", lambda s, root=None: [DENIED])
    monkeypatch.setattr("backstop._escalated", lambda s, root=None: False)
    monkeypatch.setattr("backstop._bump_fires", lambda s: 4)
    assert main(["backstop.py", SID]) == 0


def test_hook_with_no_session_id_is_quiet():
    assert main(["backstop.py"]) == 0


# ── the cap is per-session, because a global one became a permanent kill switch ────────
#
# Found 2026-07-27 at 47 against a MAX_FIRES of 3: the backstop had been silently dead, and
# rc=0 reads exactly like "nothing to report". MAX_FIRES was written as loop-safety for ONE
# session ("a backstop that can wedge a session gets ripped out"); against a monotonic global
# counter it outlived every session it was protecting. Almost certainly burned through by the
# test-manufactured denials fixed the same day.

def _fires_at(tmp_path, monkeypatch):
    path = tmp_path / ".spend-backstop-fires"
    monkeypatch.setattr("backstop.FIRE_COUNT", path)
    return path


def test_a_new_session_starts_from_zero(tmp_path, monkeypatch):
    """THE regression. A prior session at the cap must not silence the next one."""
    path = _fires_at(tmp_path, monkeypatch)
    path.write_text(json.dumps({"old-session": 99}))
    assert backstop._bump_fires("fresh-session") == 1


def test_a_legacy_global_counter_re_enables_rather_than_suppresses(tmp_path, monkeypatch):
    """The file on disk held a bare `47`. Unreadable state must fail toward the backstop
    being ALIVE — failing the other way turns a corrupt file into a silent kill switch,
    which is the bug this fixes, one level down."""
    path = _fires_at(tmp_path, monkeypatch)
    path.write_text("47")
    assert backstop._bump_fires("any-session") == 1


def test_the_cap_still_holds_within_one_session(tmp_path, monkeypatch):
    """Per-session must not mean uncapped — the loop-safety it was written for still applies."""
    _fires_at(tmp_path, monkeypatch)
    counts = [backstop._bump_fires(SID) for _ in range(5)]
    assert counts == [1, 2, 3, 4, 5]
    assert counts[-1] > backstop.MAX_FIRES


def test_a_clean_session_never_records_a_fire(tmp_path, monkeypatch):
    """The reset half: nothing to disposition means nothing is written, so a run of clean
    sessions cannot creep toward the cap."""
    path = _fires_at(tmp_path, monkeypatch)
    monkeypatch.setattr("backstop._events", lambda s, root=None: [])
    monkeypatch.setattr("backstop._escalated", lambda s, root=None: False)
    assert main(["backstop.py", SID]) == 0
    assert not path.exists()


def test_old_sessions_are_pruned(tmp_path, monkeypatch):
    """Keyed state that only grows is its own kind of leak."""
    path = _fires_at(tmp_path, monkeypatch)
    path.write_text(json.dumps({f"s{i}": i + 1 for i in range(backstop.KEEP_SESSIONS + 5)}))
    backstop._bump_fires("newest")
    assert len(json.loads(path.read_text())) <= backstop.KEEP_SESSIONS
    assert "newest" in json.loads(path.read_text())


# ── ADR-0016: the third disposition, and the escalation category ──────────────────────

def test_an_unrelated_packet_no_longer_clears_a_spend_denial(tmp_path):
    """THE TIGHTENING. _escalated() cleared on ANY packet, reasoning that an agent which
    escalated *something* while blocked had not routed around. Defensible, and the effect
    was a bypass nobody chose: this repo raises packets for all sorts of things, and each
    one silenced the spend backstop for that session by accident."""
    (tmp_path / "esc-1.json").write_text(json.dumps(
        {"id": "esc-1", "session_id": SID, "category": "design_question"}))
    assert _escalated(SID, tmp_path) is False


def test_a_spend_packet_still_clears(tmp_path):
    (tmp_path / "esc-1.json").write_text(json.dumps(
        {"id": "esc-1", "session_id": SID, "category": "spend_unauthorized"}))
    assert _escalated(SID, tmp_path) is True


def test_a_dismissal_after_the_denial_disposes_it():
    """The false-positive exit the contract promised and nothing could hear."""
    events = [DENIED, {"type": "spend_dismissed", "ts": "2030-01-01T00:00:00Z"}]
    assert undispositioned(events, escalated=False) == []


def test_a_dismissal_BEFORE_the_denial_does_not_dispose_it():
    """Same rule as a grant: a dismissal recorded earlier says nothing about a later
    denial. Otherwise one dismissal silences the rest of the session."""
    events = [{"type": "spend_dismissed", "ts": "2000-01-01T00:00:00Z"}, DENIED]
    assert len(undispositioned(events, escalated=False)) == 1
