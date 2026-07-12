"""Tests for the spend-denial backstop.

The predicate is the whole design: a denial must end in a grant or a packet. The two
"correct" paths must stay quiet, or the hook is noise and gets ripped out — and then it
protects nothing.
"""
import json

import pytest

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
    (tmp_path / "esc-1.json").write_text(json.dumps({"id": "esc-1", "session_id": SID}))
    assert _escalated(SID, tmp_path) is True
    assert _escalated("other-session", tmp_path) is False


def test_escalated_survives_a_corrupt_packet(tmp_path):
    (tmp_path / "bad.json").write_text("{ not json")
    (tmp_path / "esc-1.json").write_text(json.dumps({"id": "esc-1", "session_id": SID}))
    assert _escalated(SID, tmp_path) is True


# ── the hook contract ─────────────────────────────────────────────────────────

def test_hook_exits_2_and_names_the_command(monkeypatch, capsys):
    monkeypatch.setattr("backstop._events", lambda s, root=None: [DENIED])
    monkeypatch.setattr("backstop._escalated", lambda s, root=None: False)
    monkeypatch.setattr("backstop._bump_fires", lambda: 1)
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
    monkeypatch.setattr("backstop._bump_fires", lambda: 4)
    assert main(["backstop.py", SID]) == 0


def test_hook_with_no_session_id_is_quiet():
    assert main(["backstop.py"]) == 0
