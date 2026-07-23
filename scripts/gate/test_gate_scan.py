#!/usr/bin/env python3
"""Tests for the Stop-hook gate-scan backstop."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import scan  # noqa: E402


def _write(tmp_path, entries):
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries))
    return str(p)


def _assistant(text, sidechain=False):
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def _human(text="ok"):
    return {"type": "user", "isSidechain": False, "message": {"role": "user", "content": text}}


def _tool_use():
    return {
        "type": "assistant",
        "isSidechain": False,
        "message": {"role": "assistant", "content": [{"type": "tool_use", "name": "Bash"}]},
    }


def _tool_result():
    return {
        "type": "user",
        "isSidechain": False,
        "message": {"role": "user", "content": [{"type": "tool_result", "content": "out"}]},
    }


def test_question_then_human_is_a_gate(tmp_path):
    t = _write(tmp_path, [_human("go"), _assistant("Do A or B?"), _human()])
    assert len(scan.find_asking_turns(t)) == 1


def test_statement_then_human_is_not_a_gate(tmp_path):
    t = _write(tmp_path, [_human("go"), _assistant("Done. Fixed it."), _human()])
    assert scan.find_asking_turns(t) == []


def test_tool_result_is_not_a_human_turn(tmp_path):
    """A question followed by tool use never handed back — not a gate."""
    t = _write(tmp_path, [_human("go"), _assistant("Which?"), _tool_use(), _tool_result()])
    assert scan.find_asking_turns(t) == []


def test_question_far_from_end_is_not_asking(tmp_path):
    text = "Why does this fail?" + " x" * 400
    t = _write(tmp_path, [_human("go"), _assistant(text), _human()])
    assert scan.find_asking_turns(t) == []


def test_sidechain_turns_ignored(tmp_path):
    """Subagent transcripts share the file; their questions are not user gates."""
    t = _write(tmp_path, [_human("go"), _assistant("Sub asks?", sidechain=True), _human()])
    assert scan.find_asking_turns(t) == []


def test_multiple_gates_counted(tmp_path):
    t = _write(
        tmp_path,
        [_human("go"), _assistant("A or B?"), _human(), _assistant("C or D?"), _human()],
    )
    assert len(scan.find_asking_turns(t)) == 2


def test_malformed_lines_survive(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text('{"bad json\n' + json.dumps(_assistant("A or B?")) + "\n" + json.dumps(_human()))
    assert len(scan.find_asking_turns(str(p))) == 1


def test_missing_transcript_is_not_fatal():
    assert scan.find_asking_turns("/nonexistent/path.jsonl") == []


def test_count_logged_ignores_non_gate_events(tmp_path, monkeypatch):
    """watch.jsonl and friends share .tessera/logs/ — only suggestion_gate counts."""
    monkeypatch.setattr(scan, "LOGS", tmp_path)
    (tmp_path / "s1.jsonl").write_text(
        json.dumps({"type": "suggestion_gate"})
        + "\n"
        + json.dumps({"ts": "2026-07-11T00:00:00Z", "fired": []})
        + "\n"
    )
    assert scan.count_logged("s1") == 1


def test_count_logged_absent_file_is_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(scan, "LOGS", tmp_path)
    assert scan.count_logged("nope") == 0


def test_fires_on_gap_of_two():
    assert scan.should_fire(surfaced=5, logged=3) is True


def test_quiet_on_gap_of_one():
    """Gap of 1 is inside the over-counting detector's noise floor."""
    assert scan.should_fire(surfaced=4, logged=3) is False


def test_fires_when_nothing_logged():
    """The 100%-miss session leaves no log file — the case ratio.py cannot see."""
    assert scan.should_fire(surfaced=1, logged=0) is True


def test_quiet_when_no_gates_surfaced():
    assert scan.should_fire(surfaced=0, logged=0) is False


def test_fire_cap_silences_after_max(tmp_path, monkeypatch):
    monkeypatch.setattr(scan, "LOGS", tmp_path)
    (tmp_path / f".scan-fires-s1").write_text(str(scan.MAX_FIRES_PER_SESSION))
    monkeypatch.setattr(sys, "argv", ["scan.py", "/nonexistent", "s1"])
    assert scan.main() == 0


def test_main_exits_1_and_bumps_on_fire(tmp_path, monkeypatch):
    monkeypatch.setattr(scan, "LOGS", tmp_path)
    t = _write(tmp_path, [_human("go"), _assistant("A or B?"), _human()])
    monkeypatch.setattr(sys, "argv", ["scan.py", t, "s1"])
    assert scan.main() == 1
    assert scan._fire_count("s1") == 1


def test_question_before_tool_call_still_counts(tmp_path):
    """Ask → tool_use → sign-off statement. The gate is in the middle block.

    Caught live: scoping this very backstop, the model asked 4 questions, called
    emit.py, then closed with a statement. Last-block-only detection saw nothing.
    """
    t = _write(
        tmp_path,
        [
            _human("go"),
            _assistant("Option A or B?"),
            _tool_use(),
            _tool_result(),
            _assistant("Recorded. Waiting on you."),
            _human(),
        ],
    )
    assert len(scan.find_asking_turns(t)) == 1


# --- conclave F-001: disposition memory ------------------------------------------------


def test_turn_id_is_stable_and_whitespace_normalized():
    """Same turn, reflowed, must keep its id — else a ruling silently stops applying."""
    assert scan.turn_id("Do A or B?") == scan.turn_id("Do A   or\n  B?")
    assert scan.turn_id("Do A or B?") != scan.turn_id("Do C or D?")


def test_turn_id_hashes_whole_turn_not_the_preview():
    """Two turns sharing a 100-char tail must NOT collapse.

    This repo's asking turns routinely end the same way ('…, OK to proceed?'), so
    keying on the preview would suppress a real gate the first time its twin was ruled
    not-a-gate. Hash the whole turn.
    """
    tail = "x" * 200 + " OK to proceed?"
    assert scan.turn_id("First proposal. " + tail) != scan.turn_id("Second proposal. " + tail)


def test_load_dispositions_collects_turn_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(scan, "LOGS", tmp_path)
    (tmp_path / "s1.jsonl").write_text(
        json.dumps({"type": "gate_disposition",
                    "data": {"verdict": "not-a-gate", "turn_ids": ["aaa", "bbb"]}}) + "\n"
        + json.dumps({"type": "suggestion_gate", "data": {"fired": True}}) + "\n"
        + json.dumps({"type": "gate_disposition", "data": {"turn_ids": ["ccc"]}}) + "\n"
    )
    assert scan.load_dispositions("s1") == {"aaa", "bbb", "ccc"}


def test_load_dispositions_absent_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(scan, "LOGS", tmp_path)
    assert scan.load_dispositions("nope") == set()


def test_disposed_turns_are_not_reflagged(tmp_path, monkeypatch):
    """THE regression. Conclave hit this ~4x in one session: turns already ruled
    not-a-gate came back every Stop, so each Stop re-litigated closed decisions."""
    monkeypatch.setattr(scan, "LOGS", tmp_path)
    t = _write(tmp_path, [
        _assistant("Should I do A or B?"), _human(),
        _assistant("Reading the file now?"), _human(),
        _assistant("Ship it or wait?"), _human(),
    ])
    detected = scan.find_asking_turns(t)
    assert len(detected) == 3
    assert scan.should_fire(len(detected), 0) is True

    # Rule the middle one (narration) not-a-gate, exactly as the model would.
    narration_id = detected[1][0]
    (tmp_path / "s1.jsonl").write_text(
        json.dumps({"type": "gate_disposition",
                    "data": {"verdict": "not-a-gate", "turn_ids": [narration_id]}}) + "\n")

    disposed = scan.load_dispositions("s1")
    remaining = [x for x in detected if x[0] not in disposed]
    assert len(remaining) == 2
    assert narration_id not in {tid for tid, _ in remaining}


def test_report_shows_turn_ids_and_suppressed_count():
    """The model cannot record a ruling on a turn it cannot name."""
    out = scan.report([("abc123def456", "Ship it or wait?")], logged=0, suppressed=2)
    assert "[abc123def456]" in out
    assert "2 already ruled not-a-gate" in out
    assert "--not-a-gate --turn" in out


def test_disposition_is_not_counted_as_a_logged_gate(tmp_path, monkeypatch):
    """count_logged must not read a disposition as a logged gate — ruling turns OUT
    would otherwise inflate the logged count and mask a real miss."""
    monkeypatch.setattr(scan, "LOGS", tmp_path)
    (tmp_path / "s1.jsonl").write_text(
        json.dumps({"type": "gate_disposition", "data": {"turn_ids": ["a"]}}) + "\n")
    assert scan.count_logged("s1") == 0
