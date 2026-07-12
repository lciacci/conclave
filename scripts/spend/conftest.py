"""Suite-wide guarantee: no test may write to the production audit log.

Found 2026-07-12, by reading the log the backstop was about to fire on. `guard.main()` calls
`_log_denial()` → `event.emit()`, which keys on `CLAUDE_CODE_SESSION_ID` — and under a normal
Claude Code session that variable IS set, so **every hook test appended a real `spend_denied`
to `.tessera/logs/<session>.jsonl`.** 26 of the session's 31 denials were manufactured by the
suite: an 84%-polluted friction journal, and a backstop poised to fire on its own tests.

This is the P3/trigger-tagging lesson in a new costume: *a test must never become evidence
about the thing it tests.* There, a hand-run `/compact` could have delivered the Mnemos
verdict on manufactured data. Here, pytest was manufacturing the spend journal.

The fix is at the root, not per-test: strip the session id and `emit()` is inert by
construction (it returns None with nothing to key on). No future test can pollute the log by
forgetting to mock something.
"""
import pytest


@pytest.fixture(autouse=True)
def no_audit_writes(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
