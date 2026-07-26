"""Self-check for the gate-event recorder.

Run from this dir: python3 test_gate_emit.py

Asserts build_event produces a contract-shaped event (docs/contracts/gate-event.md):
discriminator, null should_fire, no invented score/threshold, optional note.
"""

import json
from pathlib import Path

from emit import build_event


def demo() -> None:
    e = build_event(True, "refactor", "make contract canonical",
                    session_id="sess-1", ts="2026-06-23T18:25:00Z")

    # Top-level shape.
    assert e["type"] == "suggestion_gate"
    assert e["session_id"] == "sess-1"
    assert e["ts"] == "2026-06-23T18:25:00Z"
    assert e["source"] == "suggestion-gate-recorder"

    d = e["data"]
    assert d["fired"] is True                 # strict boolean, not truthy
    assert d["suggestion_kind"] == "refactor"
    assert d["note"] == "make contract canonical"
    assert d["should_fire"] is None           # labeled post-hoc, never invented
    assert "score" not in d                    # no scorer → absent, not faked
    assert "threshold" not in d

    # held + no note: note omitted entirely, fired is a real False.
    h = build_event(False, "compact", None, session_id="s")
    assert h["data"]["fired"] is False
    assert "note" not in h["data"]

    # retro: present-and-true only when set — absent means ts IS the gate moment.
    assert "retro" not in e["data"]
    r = build_event(True, "scope", None, session_id="s", retro=True)
    assert r["data"]["retro"] is True

    # Kind vocabulary (spec 15): CLI rejects unknown kinds, fail-closed, exit 2.
    import os
    from emit import KINDS, main
    os.environ.setdefault("CLAUDE_CODE_SESSION_ID", "test-session")
    assert main(["--fired", "--kind", "refactor", "--dry-run"]) == 2  # pre-enum kind
    assert main(["--fired", "--kind", KINDS[0], "--dry-run"]) == 0

    # Remap: legacy → canonical with raw kept; canonical and unknown untouched.
    from remap_kind import remap_line
    legacy = json.dumps({"type": "suggestion_gate", "data":
                         {"suggestion_kind": "design-decision", "should_fire": None}})
    new, change = remap_line(legacy)
    d = json.loads(new)["data"]
    assert d["suggestion_kind"] == "design" and d["suggestion_kind_raw"] == "design-decision"
    assert change == "design-decision→design"
    assert remap_line(new)[1] is None                 # idempotent — _raw present
    mystery = json.dumps({"type": "suggestion_gate", "data": {"suggestion_kind": "zzz"}})
    assert remap_line(mystery) == (mystery, "unknown:zzz")  # reported, not guessed

    # Round-trips as one JSONL line.
    assert json.loads(json.dumps(e)) == e

    print("ok")


if __name__ == "__main__":
    demo()


# --- conclave F-001: the disposition recorder -------------------------------------------

import emit  # noqa: E402


def test_not_a_gate_writes_a_disposition_event(tmp_path, monkeypatch):
    # TESSERA_ROOT, not chdir: the log is anchored to the repo now, so a bare chdir would
    # send this write into the REAL .tessera/logs/. It did, once, while this was being built.
    monkeypatch.setenv("TESSERA_ROOT", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1")
    assert emit.main(["--not-a-gate", "--turn", "abc123", "--turn", "def456",
                      "--note", "both were clarifying questions"]) == 0
    logged = json.loads((tmp_path / ".tessera/logs/s1.jsonl").read_text().strip())
    assert logged["type"] == "gate_disposition"
    assert logged["data"]["verdict"] == "not-a-gate"
    assert logged["data"]["turn_ids"] == ["abc123", "def456"]


def test_log_path_ignores_a_foreign_cwd(tmp_path, monkeypatch):
    """The 2026-07-24 4/2 gate-log split, as a regression test.

    A `cd` into a downstream persists across Bash calls, and emit.py has no hook wrapper
    to cd back. Anchored on the repo, the destination must not move when the cwd does.
    """
    monkeypatch.delenv("TESSERA_ROOT", raising=False)
    anchored = emit._log_path("s1")
    monkeypatch.chdir(tmp_path)
    assert emit._log_path("s1") == anchored, "cwd leaked into the gate-log path"
    assert anchored.is_absolute()
    # ...and the anchor is this repo, not wherever the tool happened to be run from.
    assert anchored == Path(__file__).resolve().parents[2] / ".tessera/logs/s1.jsonl"


def test_tessera_root_overrides_the_anchor(tmp_path, monkeypatch):
    """The deliberate cross-repo run stays possible — and is how tests stay off the real log."""
    monkeypatch.setenv("TESSERA_ROOT", str(tmp_path))
    assert emit._log_path("s1") == tmp_path / ".tessera/logs/s1.jsonl"


def test_not_a_gate_requires_a_turn_id(tmp_path, monkeypatch):
    """A disposition with no target is a shrug, not a ruling."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1")
    assert emit.main(["--not-a-gate", "--note", "nope"]) == 2


def test_fired_still_requires_kind(tmp_path, monkeypatch):
    """--kind stopped being argparse-required so --not-a-gate could omit it; the
    requirement must still hold for the gate paths."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1")
    assert emit.main(["--fired", "--note", "x"]) == 2
