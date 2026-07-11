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
