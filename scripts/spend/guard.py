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
SCRIPT_TOKEN = re.compile(r"[\w./~-]+\.(?:sh|py|bash)\b")
MAX_SCRIPT_BYTES = 64_000

# ponytail: ONE level, no recursion — a script that calls a script that boots a GPU still
# slips through, as does anything using a cloud SDK (boto3 `run_instances`) rather than the
# CLI. This is a recall net, not an oracle; the cloud budget (layer 3) is what bounds the
# misses. Add patterns as new spend surfaces appear — a miss is a finding about this list.


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
    """Classify the body of any local script this segment invokes. None if it invokes none."""
    for token in SCRIPT_TOKEN.findall(segment):
        body = _script_body(token)
        if not body:
            continue
        # Classify the script's own lines, comments stripped — a commented-out
        # `terraform apply` in a script is a mention, not a boot.
        lines = [ln for ln in body.splitlines() if not ln.lstrip().startswith("#")]
        for line in lines:
            if COMMITTING.search(line) and not REDUCING.search(line):
                return "committing"
    return None


def _segments(command: str) -> list[str]:
    """Split on shell separators before classifying.

    Without this, `terraform destroy && terraform apply` classifies as REDUCING on the
    first match and the apply rides through free. Committing wins across segments.
    """
    return [s for s in re.split(r"&&|\|\||[;\n|]", command) if s.strip()]


def _classify_one(segment: str) -> str:
    if REDUCING.search(segment):
        return "reducing"
    if COMMITTING.search(segment):
        return "committing"
    return _invoked_script_kind(segment) or "neutral"


def classify(command: str) -> str:
    """-> 'committing' | 'reducing' | 'neutral'. Committing wins; it is the safe direction."""
    kinds = {_classify_one(s) for s in _segments(command)}
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
