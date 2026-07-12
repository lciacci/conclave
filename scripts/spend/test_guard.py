"""Tests for the external-spend guard (spec 06).

The cases that matter are the two that would hurt: the teardown-blocking flaw the
original spec shipped, and the `destroy && apply` bypass that a naive first-match
classifier waves through.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from authorize import build_grant, parse_ttl
from guard import classify, decide, is_live, load_auth, main

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def live(usd=20.0, hours=4):
    return build_grant(usd, timedelta(hours=hours), "test run", NOW)


def live_now(hours=4):
    """A grant live against the REAL clock — main() reads datetime.now(), not our fake NOW,
    so pinning expiry to NOW makes these tests pass or fail depending on the wall clock."""
    return build_grant(20.0, timedelta(hours=hours), "test run", datetime.now(timezone.utc))


def expired():
    return build_grant(20.0, timedelta(hours=1), "test run", NOW - timedelta(hours=4))


# ── classification ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cmd", [
    "terraform apply -var enable_gpu=true",
    "terraform apply",                                    # bare: tfvars may set gpu true
    "cd infra && terraform apply -auto-approve",
    "aws ec2 run-instances --instance-type g6e.xlarge",
    "aws ec2 start-instances --instance-ids i-abc",
    "aws ec2 request-spot-instances --spot-price 0.50",
])
def test_committing(cmd):
    assert classify(cmd) == "committing"


@pytest.mark.parametrize("cmd", [
    "terraform destroy -auto-approve",
    "terraform apply -var enable_gpu=false",              # an apply that TEARS DOWN
    'terraform apply -var="enable_gpu=false"',
    "aws ec2 stop-instances --instance-ids i-abc",
    "aws ec2 terminate-instances --instance-ids i-abc",
])
def test_reducing(cmd):
    assert classify(cmd) == "reducing"


@pytest.mark.parametrize("cmd", [
    "terraform plan",                                     # read-only, commits nothing
    "terraform fmt -check",
    "aws ec2 describe-instances",
    "pytest -q",
    "git status",
])
def test_neutral(cmd):
    assert classify(cmd) == "neutral"


def test_chained_destroy_then_apply_is_committing():
    """The bypass. A first-match classifier sees `destroy`, says 'reducing', and lets
    the apply boot a GPU for free. Committing must win across segments."""
    assert classify("terraform destroy && terraform apply -var enable_gpu=true") == "committing"
    assert classify("echo hi; terraform apply") == "committing"
    assert classify("terraform plan | tee out && terraform apply") == "committing"


# ── the invariant: a spend gate must never block the exit ─────────────────────

@pytest.mark.parametrize("cmd", [
    "terraform destroy -auto-approve",
    "terraform apply -var enable_gpu=false",
    "aws ec2 stop-instances --instance-ids i-abc",
])
def test_teardown_always_allowed_even_with_no_authorization(cmd):
    """Spec 06 as originally written rejected ALL Bash on budget overrun, which would
    have frozen an agent with a live GPU and blocked its own teardown — causing the
    runaway it existed to prevent. Teardown is unconditional."""
    allow, reason = decide(cmd, None, NOW)
    assert allow is True
    assert "always allowed" in reason


def test_teardown_allowed_with_expired_authorization():
    allow, _ = decide("terraform destroy", expired(), NOW)
    assert allow is True


# ── authorization ─────────────────────────────────────────────────────────────

def test_committing_blocked_without_authorization():
    allow, reason = decide("terraform apply", None, NOW)
    assert allow is False
    assert "no live authorization" in reason


def test_committing_allowed_with_live_authorization():
    allow, reason = decide("terraform apply -var enable_gpu=true", live(), NOW)
    assert allow is True
    assert "$20.0" in reason


def test_committing_blocked_with_expired_authorization():
    allow, _ = decide("terraform apply", expired(), NOW)
    assert allow is False


def test_authorization_expires_exactly_at_ttl():
    auth = live(hours=4)
    assert is_live(auth, NOW + timedelta(hours=3, minutes=59)) is True
    assert is_live(auth, NOW + timedelta(hours=4, minutes=1)) is False


def test_neutral_allowed_without_authorization():
    assert decide("pytest -q", None, NOW)[0] is True


# ── fail-closed on a broken grant ─────────────────────────────────────────────

def test_corrupt_grant_is_not_a_grant(tmp_path):
    p = tmp_path / "spend-auth.json"
    p.write_text("{ not json")
    assert load_auth(p) is None
    assert decide("terraform apply", load_auth(p), NOW)[0] is False


def test_missing_grant_file_is_not_a_grant(tmp_path):
    assert load_auth(tmp_path / "nope.json") is None


def test_grant_with_no_expiry_is_not_live():
    assert is_live({"usd": 20}, NOW) is False


def test_grant_with_unparseable_expiry_is_not_live():
    assert is_live({"expires_at": "whenever"}, NOW) is False


# ── ttl parsing ───────────────────────────────────────────────────────────────

def test_parse_ttl():
    assert parse_ttl("4h") == timedelta(hours=4)
    assert parse_ttl("90m") == timedelta(minutes=90)


@pytest.mark.parametrize("bad", ["4", "4d", "-2h", "0h", "", "forever"])
def test_parse_ttl_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_ttl(bad)


# ── the hook contract: exit codes ─────────────────────────────────────────────

def _run_hook(monkeypatch, capsys, command, auth):
    monkeypatch.setattr("guard.load_auth", lambda path=None: auth)
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps({"tool_input": {"command": command}})))
    code = main()
    return code, capsys.readouterr().err


class _Stdin:
    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text


def test_hook_exits_2_and_explains_when_blocking(monkeypatch, capsys):
    code, err = _run_hook(monkeypatch, capsys, "terraform apply", None)
    assert code == 2
    assert "BLOCKED" in err
    assert "tessera-escalate raise" in err     # the agent is told where to go
    assert "tessera-authorize grant" in err


def test_hook_exits_0_on_teardown(monkeypatch, capsys):
    assert _run_hook(monkeypatch, capsys, "terraform destroy", None)[0] == 0


def test_hook_exits_0_when_authorized(monkeypatch, capsys):
    assert _run_hook(monkeypatch, capsys, "terraform apply", live_now())[0] == 0


def test_hook_exits_2_when_authorization_expired(monkeypatch, capsys):
    assert _run_hook(monkeypatch, capsys, "terraform apply", expired())[0] == 2


def test_hook_ignores_junk_stdin(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", _Stdin("not json"))
    assert main() == 0
    monkeypatch.setattr("sys.stdin", _Stdin(""))
    assert main() == 0


# ── regression: the real commands from conclave's gate corpus ─────────────────

@pytest.mark.parametrize("cmd,kind", [
    ("terraform apply -var enable_gpu=true -var use_spot=false", "committing"),
    ("terraform apply -var enable_gpu=true", "committing"),
    ("terraform apply -var enable_gpu=false", "reducing"),
])
def test_real_conclave_gate_commands(cmd, kind):
    """Taken from the 14 aws-launch/teardown/spend gates in conclave's log — the corpus
    that promoted this spec to Tier 1 in the first place."""
    assert classify(cmd) == kind


# ── wrapper scripts: the hole found live-firing in conclave ───────────────────
# conclave's `scripts/sweep-gpu-capacity.sh` runs `terraform apply -auto-approve` on line 23.
# It boots g6e GPUs; it is the AZ-sweep named in the gate log. The guard sees only the
# wrapper's NAME, so a name-only classifier waves a real GPU boot straight through. This was
# live in the flagship downstream until 2026-07-12.

def _script(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body)
    p.chmod(0o755)
    return p


def test_wrapper_script_that_boots_a_gpu_is_committing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _script(tmp_path, "sweep.sh", "#!/bin/bash\nTF_LOG=DEBUG terraform apply -auto-approve\n")
    assert classify("./sweep.sh") == "committing"
    assert classify("bash sweep.sh --az us-east-1c") == "committing"
    assert decide("./sweep.sh", None, NOW)[0] is False


def test_wrapper_script_that_only_tears_down_keeps_the_exit_open(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _script(tmp_path, "teardown.sh", "#!/bin/bash\nterraform apply -var enable_gpu=false\n")
    assert classify("./teardown.sh") != "committing"
    assert decide("./teardown.sh", None, NOW)[0] is True


def test_commented_out_spend_in_a_script_is_a_mention_not_a_boot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _script(tmp_path, "notes.sh", "#!/bin/bash\n# terraform apply -var enable_gpu=true\necho hi\n")
    assert classify("./notes.sh") == "neutral"


def test_script_that_does_not_exist_is_neutral(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert classify("./nope.sh") == "neutral"


def test_harmless_script_is_neutral(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _script(tmp_path, "build.sh", "#!/bin/bash\nnpm run build\n")
    assert classify("./build.sh") == "neutral"


def test_env_prefixed_invocation_is_still_read(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _script(tmp_path, "sweep.sh", "#!/bin/bash\nterraform apply -auto-approve\n")
    assert classify("TF_LOG=DEBUG AWS_PROFILE=x ./sweep.sh") == "committing"


@pytest.mark.parametrize("cmd", [
    "cp sweep.sh /tmp/sweep.sh",
    "git add sweep.sh",
    "cat sweep.sh",
    "vim sweep.sh",
    "wc -l sweep.sh",
])
def test_naming_a_script_is_not_invoking_it(tmp_path, monkeypatch, cmd):
    """Found live: `cp guard.py test_guard.py` was BLOCKED, because the reader opened the test
    file and found a boot command quoted in a fixture. Copying, staging, or reading a script
    does not run it. The script must be in COMMAND POSITION or it is just a filename."""
    monkeypatch.chdir(tmp_path)
    _script(tmp_path, "sweep.sh", "#!/bin/bash\nterraform apply -auto-approve\n")
    assert classify(cmd) == "neutral"


# ── mention vs invocation ─────────────────────────────────────────────────────
# Quoted strings and heredoc bodies are DATA — unless a wrapper executes them. Drawing that
# line is what separates "the agent boots a GPU" from "the agent writes about booting a GPU".
#
# The four below are the real false positives this guard produced against its own author on
# 2026-07-12, in one session: a test heredoc, the command that installed it into conclave, the
# commit message describing it, and the gate-log call describing the false positive. Each one
# blocked work that committed no spend whatsoever.

def test_mention_in_a_quoted_argument_is_not_an_invocation():
    assert classify('grep -r "terraform apply" .') == "neutral"
    assert classify('git commit -m "feat(gpu): wrap terraform apply"') == "neutral"
    assert classify('python3 emit.py --note "blocked on terraform apply"') == "neutral"


def test_heredoc_body_fed_to_a_non_executing_command_is_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert classify("cat >> test_x.py <<'PY'\n# terraform apply -var enable_gpu=true\nPY") == "neutral"
    assert classify("git commit -F - <<'MSG'\nfeat: gate terraform apply\nMSG") == "neutral"


# ── the wrapper bypasses stay closed ──────────────────────────────────────────
# Softening the classifier is only safe because these still fire. A segment that EXECUTES its
# own literal text is not stripped: there, the quotes hold code, not prose.

@pytest.mark.parametrize("cmd", [
    'bash -c "terraform apply -var enable_gpu=true"',
    'sh -c "terraform apply"',
    "eval 'terraform apply'",
    'python3 -c "import os; os.system(\'terraform apply\')"',
    "bash <<'EOF'\nterraform apply -var enable_gpu=true\nEOF",
    "python3 - <<'PY'\nsubprocess.run('terraform apply', shell=True)\nPY",
])
def test_wrapper_executing_its_own_literal_is_still_committing(cmd):
    assert classify(cmd) == "committing"


def test_unquoted_invocation_is_unaffected_by_the_softening():
    assert classify("terraform apply -var enable_gpu=true") == "committing"
    assert classify("cd infra && terraform apply") == "committing"


def test_softening_never_narrows_the_reducing_check(tmp_path, monkeypatch):
    """THE INVARIANT under the new rule. REDUCING is matched on the RAW segment, so a quoted
    `enable_gpu=false` still reads as teardown. If quote-stripping were applied to BOTH checks,
    `terraform apply -var="enable_gpu=false"` would strip to `terraform apply -var=` and the
    guard would block a teardown — reintroducing the exact flaw this spec was retargeted to
    kill."""
    monkeypatch.chdir(tmp_path)
    assert classify('terraform apply -var="enable_gpu=false"') == "reducing"
    assert decide('terraform apply -var="enable_gpu=false"', None, NOW)[0] is True
