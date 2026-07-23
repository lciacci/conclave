#!/usr/bin/env python3
"""Deny-by-default gate on external, irreversible spend (spec 06).

ADR-0005: 56% of conclave's gates are `terraform apply` / `terraform destroy` against
g6e GPUs. An unsupervised agent there is an agent that boots GPUs on its own. Every one
of those gates was a human saying yes to one specific boot; delete the human and nothing
stands between the agent and the instance.

This is the *authorization* layer, not an accounting layer. Tessera does not meter
dollars — AWS does, and it is the only system that knows the real number (spot vs
on-demand, AZ, actual duration). Tessera gates on **is this run authorized to commit
external spend at all**, and enforces a **TTL**, which is the one bound it can honestly
hold. For a GPU, cost is ~linear in time, so a time-boxed envelope *is* a spend bound.

Three layers, three trust domains:
  1. this guard          — in-band, pre-commitment authorization
  2. tessera-escalate    — the async human gate when authorization is absent (spec 07)
  3. conclave's AWS layer— out-of-band blast-radius backstop (budget.tf/hardstop.tf/idle-stop)

THE LOAD-BEARING INVARIANT — see `decide()`: **cost-reducing commands are never blocked.**
Spec 06 originally hard-stopped by rejecting all Bash on budget overrun; that would have
frozen an agent with a GPU running and blocked its own teardown, causing the exact runaway
it existed to prevent. A spend gate must never be able to block the exit.

Contract: docs/contracts/spend-authorization.md
"""

# ── Runs on ANY python3, including macOS's /usr/bin/python3 (3.9). LOAD-BEARING. ──────────
# This hook must keep working when the venv is broken, so it runs on bare `python3`. On
# 2026-07-12 that reasoning was proved half-right, and dangerously: **stdlib-only is NOT
# version-independent.** When the interpreter NAME drifts, the VERSION drifts with it. On a
# /usr/bin-first PATH `python3` is 3.9; PEP-604 annotations (`str | None`) raise TypeError at
# definition time; the script exits 1; and the hook wrapper passes that through as "not 2",
# i.e. **ALLOW**. An unauthorized GPU boot proceeded, silently.
# `from __future__ import annotations` makes annotations lazy strings. DO NOT REMOVE.
from __future__ import annotations
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

AUTH_PATH = Path(".tessera/spend-auth.json")

# Cost-REDUCING: always allowed, unconditionally. Checked first within a segment so
# `terraform apply -var enable_gpu=false` reads as the teardown it is.
REDUCING = re.compile(
    r"terraform\s+destroy"
    r"|enable_gpu\s*=\s*[\"']?false"
    r"|aws\s+ec2\s+(stop|terminate)-instances",
    re.IGNORECASE,
)

# Spend-COMMITTING: gated. A bare `terraform apply` counts — enable_gpu may be true in a
# .tfvars we cannot see from the command line, so the safe read is "this may boot."
COMMITTING = re.compile(
    r"terraform\s+apply"
    r"|aws\s+ec2\s+(run|start)-instances"
    r"|aws\s+ec2\s+(create-fleet|request-spot-instances)",
    re.IGNORECASE,
)

# A local script the command invokes. The guard only ever sees the Bash command STRING, so
# `./scripts/sweep-gpu-capacity.sh` is opaque — and conclave's sweep script runs
# `terraform apply -auto-approve` on line 23. It boots g6e GPUs, it is the AZ-sweep from the
# gate log, and a name-only classifier waves it straight through. Found 2026-07-12 while
# live-firing this guard in conclave; it is exactly the "wrapper script slips through" ceiling
# the contract predicted, and it was live in the flagship downstream. So: read one level down.
# The script must be in COMMAND POSITION — first token of the segment, after any leading env
# assignments, optionally behind an interpreter. `cp a.py b.py`, `git add x.sh`, `cat x.sh` and
# `vim x.sh` all NAME a script without running it, and reading their contents to classify the
# command is a category error: it blocked `cp guard.py test_guard.py` because the *test file*
# quotes a boot command. Naming is not invoking.
INVOKED_SCRIPT = re.compile(
    r"^\s*(?:\w+=\S*\s+)*"                                        # FOO=bar BAZ=qux …
    r"(?:(?:bash|sh|zsh|dash|python3?|perl|ruby|node)\s+)?"       # … optional interpreter
    r"([\w./~-]+\.(?:sh|py|bash))\b"                              # … the script itself
)
MAX_SCRIPT_BYTES = 64_000

# ponytail: ONE level, no recursion — a script that calls a script that boots a GPU still
# slips through, as does anything using a cloud SDK (boto3 `run_instances`) rather than the
# CLI. This is a recall net, not an oracle; the cloud budget (layer 3) is what bounds the
# misses. Add patterns as new spend surfaces appear — a miss is a finding about this list.

# A segment that EXECUTES its own literal text: a shell taking `-c`, an interpreter taking
# `-c` or `-` (stdin), `eval`, `xargs`, or a bare shell reading a heredoc. Quoted strings and
# heredoc bodies are stripped before the committing check EVERYWHERE ELSE — but not here,
# because here they are code, not data. This is the line between a *mention* and an
# *invocation*, and it is the only place that distinction can be drawn honestly.
WRAPPER = re.compile(
    r"^\s*(?:eval|xargs|bash|sh|zsh|dash)\b"
    r"|\b(?:bash|sh|zsh|dash|python3?|perl|ruby|node)\s+-c\b"
    r"|\bpython3?\s+-(?:\s|$)",
    re.IGNORECASE,
)
QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
HEREDOC_START = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")


def _strip_heredocs(command: str) -> str:
    """Drop heredoc BODIES — they are data being written, not code being run.

    Only ever called when the command is NOT wrapper-led, so `bash <<'EOF' … EOF` never
    reaches here. `cat >> test.py <<'PY' … PY` does, and its body is a file being written.
    """
    lines, out, i = command.splitlines(), [], 0
    while i < len(lines):
        out.append(lines[i])
        m = HEREDOC_START.search(lines[i])
        i += 1
        if not m:
            continue
        while i < len(lines) and lines[i].strip() != m.group(1):
            i += 1
        i += 1  # consume the delimiter
    return "\n".join(out)


def _script_body(token: str) -> str:
    """The contents of a local script the command invokes, or ''. Never raises."""
    try:
        path = Path(token.strip()).expanduser()
        if not path.is_file() or path.stat().st_size > MAX_SCRIPT_BYTES:
            return ""
        return path.read_text(errors="replace")
    except (OSError, ValueError):
        return ""


def _invoked_script_kind(segment: str) -> str | None:
    """Classify the body of the local script this segment INVOKES. None if it invokes none."""
    m = INVOKED_SCRIPT.match(segment)
    if not m:
        return None
    body = _script_body(m.group(1))
    # Comments stripped — a commented-out boot command in a script is a mention, not a boot.
    for line in body.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if COMMITTING.search(line) and not REDUCING.search(line):
            return "committing"
    return None


def _segments(command: str) -> list[str]:
    """Split on shell separators before classifying.

    Without this, `terraform destroy && terraform apply` classifies as REDUCING on the
    first match and the apply rides through free. Committing wins across segments.
    """
    return [s for s in re.split(r"&&|\|\||[;\n|]", command) if s.strip()]


def _classify_one(segment: str, wrapped: bool) -> str:
    # REDUCING is checked on the RAW segment, quotes and all. A quoted `enable_gpu=false` is
    # still a teardown, and the exit must never be blocked — so this check never gets narrower
    # than the committing one. That ordering IS the invariant.
    if REDUCING.search(segment):
        return "reducing"
    executable = segment if wrapped else QUOTED.sub(" ", segment)
    if COMMITTING.search(executable):
        return "committing"
    return _invoked_script_kind(segment) or "neutral"


def classify(command: str) -> str:
    """-> 'committing' | 'reducing' | 'neutral'. Committing wins; it is the safe direction.

    Quoted text and heredoc bodies are DATA — a mention, not an invocation — and are stripped
    before the committing check. UNLESS the command is wrapper-led, in which case they are code
    and nothing is stripped.

    Wrapper-ness is decided on the WHOLE command, never per segment: `python3 -c "a; b"` splits
    on the `;` *inside its own quotes*, and the resulting fragment no longer looks like a
    wrapper. Judging each fragment in isolation reopened the bypass this softening was only
    safe without. Global, and conservative — if any part of the command can execute its own
    literals, none of it gets stripped.
    """
    wrapped = bool(WRAPPER.search(command))
    text = command if wrapped else _strip_heredocs(command)
    kinds = {_classify_one(s, wrapped) for s in _segments(text)}
    if "committing" in kinds:
        return "committing"
    if "reducing" in kinds:
        return "reducing"
    return "neutral"


def load_auth(path: Path | None = None) -> dict | None:
    """The live grant, or None. A corrupt/unreadable grant is NOT a grant.

    Fails CLOSED on purpose: this is the one place in Tessera where an unreadable file
    must not be shrugged off. No authorization == no spend.
    """
    path = path or AUTH_PATH
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def is_live(auth: dict | None, now: datetime) -> bool:
    if not auth:
        return False
    try:
        expires = datetime.fromisoformat(auth["expires_at"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError):
        return False  # a grant with no readable expiry is not a grant
    return now < expires


def decide(command: str, auth: dict | None, now: datetime) -> tuple[bool, str]:
    """-> (allow, reason). The whole policy, in one readable place."""
    kind = classify(command)
    if kind == "reducing":
        return True, "cost-reducing — always allowed, never blocked"
    if kind == "neutral":
        return True, "no external spend committed"
    if is_live(auth, now):
        return True, f"authorized: ${auth.get('usd')} until {auth['expires_at']}"
    return False, "spend-committing command with no live authorization"


DENIAL = """BLOCKED — spend-committing command with no live authorization.

  {command}

This command commits external, irreversible spend (spec 06 / ADR-0005). Teardown and
other cost-reducing commands are never blocked — only commitment is.

If a human is present, ask for an envelope:
  bin/tessera-authorize grant --usd <n> --ttl 4h --note "<what this run needs to boot>"

If you are running unsupervised, do NOT retry and do NOT route around this. Raise a packet
and stop:
  bin/tessera-escalate raise --category spend_unauthorized \\
      --summary "blocked: {short}" \\
      --tried "spend guard denied — no live authorization in .tessera/spend-auth.json" \\
      --option "grant an envelope with tessera-authorize" \\
      --option "run the offline path instead, if one exists"
"""


def main() -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        command = json.loads(raw).get("tool_input", {}).get("command", "")
    except json.JSONDecodeError:
        return 0  # not a shape we understand — the AWS layer is the backstop
    if not command:
        return 0

    allow, reason = decide(command, load_auth(), datetime.now(timezone.utc))
    if allow:
        return 0

    short = command.strip().splitlines()[0][:60]
    sys.stderr.write(DENIAL.format(command=command.strip(), short=short))
    _log_denial(command, reason)
    return 2


def _log_denial(command: str, reason: str) -> None:
    """Audit trail. Best-effort — a failed log must never turn a deny into an allow."""
    try:
        from event import emit  # noqa: PLC0415  (same-dir import, see run-tests.sh header)

        emit("spend_denied", {"command": command[:200], "reason": reason})
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
