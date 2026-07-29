#!/usr/bin/env python3
"""S2 MODEL-AXIS probe — does MODEL diversity add union-recall over ROLE diversity alone?

WHY THIS EXISTS, AND WHY IT IS NOT "S2".
`docs/S2-scoping.md` PARKED the S2 build for three reasons, each called sufficient: the number the
port produces already exists (thin), the real lever (more seeds) is pr-arbiter's lane, and everything
downstream is ADR-gated. That park still stands and this does not undo it. What this IS is the one
variant that memo's addendum carves out as adding NEW evidence rather than porting pr-arbiter's
existing role-only number: the MODEL axis.

THE QUESTION. pr-arbiter's anti-conflation guard (b) says "one strong model, role-differentiated
prompts, NO fleet". Its EVIDENCE is conclave's SELECT-BEST null (a model fleet does not beat the best
single model on Q&A). But the adversarial path optimises UNION-RECALL, which does not saturate the
same way — guard (b) is therefore ASSERTED for review and MEASURED only for select-best. Whether a
second MODEL adds recall on the review path is genuinely open.

THE DESIGN, AND THE CONFOUND IT AVOIDS. Conclave has already been burned by exactly one mistake here:
the Self-MoA "+0.0977" turned out to be mostly CANDIDATE COUNT, not diversity (+0.0371 was just extra
draws). So the arms are matched at TWO PASSES each:

    role-diverse   claude-reviewer  UNION  claude-arbiter    (different ROLE, same MODEL)
    model-diverse  claude-reviewer  UNION  qwen-reviewer     (same ROLE, different MODEL)

Same union operator, same scorer, same corpus, two passes each. The only thing that varies between
the arms is what the second pass differs by. Adding qwen as a THIRD pass on top of the role-diverse
union would raise recall trivially and prove nothing.

BOUNDARIES HONOURED (from docs/S2-scoping.md):
  1. Emits the MEASUREMENT, never a go/no-go threshold — a cutoff is routing policy, Tessera's lane.
  2. Does NOT define "true finding" solo: imports pr-arbiter's `_approximate_match` and their
     `expected_findings`. Using theirs is the co-ownership-safe path.
  3. Does NOT reproduce select-best; the oracle is union-of-true-findings.
  4. Does NOT rewrite guard (b). Rewriting it is an ADR-level cross-repo decision. This RECORDS.

Reads pr-arbiter read-only. Writes only into conclave.

    python3 orchestrator/s2_model_axis.py --gen     # generate qwen reviewer passes (local, $0)
    python3 orchestrator/s2_model_axis.py           # score whatever exists
    python3 orchestrator/s2_model_axis.py --demo    # offline self-check, no Ollama
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ARBITER_REPO = os.environ.get("PR_ARBITER_REPO",
                             os.path.join(os.path.dirname(HERE), "..", "pr-arbiter"))
ARBITER_REPO = os.path.abspath(ARBITER_REPO)
OUT = os.path.join(HERE, "s2_model_axis_qwen_passes.json")

OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://localhost:11434")
MODEL = os.environ.get("LOCAL_CODER", "qwen3-coder:30b")
TIMEOUT = float(os.environ.get("CONCLAVE_TIMEOUT", "600"))
MAX_TOKENS = int(os.environ.get("CONCLAVE_MAX_TOKENS", "4096"))

sys.path.insert(0, ARBITER_REPO)
from eval.harness import _approximate_match, load_agent_input, load_rubric, list_pr_ids  # noqa: E402


def _reviewer_system() -> str:
    """pr-arbiter's reviewer system prompt, VERBATIM, read from their source.

    Read via ast rather than imported: agents/reviewer.py imports the Anthropic client at module
    level, which is not a dependency of this repo. Parsing the assignment keeps the prompt canonical
    (it still comes from their file, so it cannot silently drift from what claude was given) without
    dragging in an API SDK to obtain a string.

    Giving the qwen pass a DIFFERENT task than the claude pass would make this measure prompt
    engineering rather than model diversity, so the prompt must be theirs, not a paraphrase.
    """
    import ast
    src = open(os.path.join(ARBITER_REPO, "agents", "reviewer.py")).read()
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "REVIEWER_SYSTEM" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise RuntimeError("REVIEWER_SYSTEM not found in pr-arbiter/agents/reviewer.py")

# The tool-use schema is an Anthropic mechanism; Ollama gets the same contract as plain JSON.
JSON_INSTRUCTION = """

Return ONLY a JSON object, no prose, no markdown fence:
{"findings": [{"file": "after.py", "line_range": [10, 12], "category": "security",
               "severity": "critical", "description": "...", "rationale": "..."}]}
Categories: security | correctness | style. Severities: critical | high | medium | low.
Return {"findings": []} if you find no real issues."""


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply. Weak models fence or preamble."""
    t = text.strip()
    if "```" in t:
        parts = t.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                t = p
                break
    start = t.find("{")
    if start < 0:
        return {"findings": []}
    depth = 0
    for i, ch in enumerate(t[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(t[start:i + 1])
                except json.JSONDecodeError:
                    return {"findings": []}
    return {"findings": []}


def gen() -> dict[str, list[dict]]:
    """Generate qwen reviewer passes over the corpus. Resumes; incremental writes."""
    from ensemble import http_call

    cache: dict[str, list[dict]] = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            cache = json.load(f)

    for pr_id in list_pr_ids():
        if pr_id in cache:
            continue
        inp = load_agent_input(pr_id)          # NEVER load_rubric here — that would leak the answers
        user = (f"Review this pull request.\n\nDIFF:\n```\n{inp['diff']}\n```\n\n"
                f"AFTER STATE ({inp['lang']}):\n```{inp['lang']}\n{inp['after']}\n```")
        msgs = [{"role": "system", "content": _reviewer_system() + JSON_INSTRUCTION},
                {"role": "user", "content": user}]
        t0 = time.monotonic()
        try:
            raw = http_call(OLLAMA_BASE, MODEL, msgs, TIMEOUT, max_tokens=MAX_TOKENS)
            findings = _extract_json(raw).get("findings", []) or []
        except Exception as e:                                   # noqa: BLE001
            print(f"  {pr_id}: ERROR {type(e).__name__}: {e}", file=sys.stderr)
            continue
        cache[pr_id] = findings
        with open(OUT, "w") as f:
            json.dump(cache, f, indent=2)
        print(f"  {pr_id}: {len(findings)} findings, {time.monotonic() - t0:.1f}s")
    return cache


def _recall(pr_findings: dict[str, list[dict]]) -> dict:
    """Union-recall over the corpus, scored with pr-arbiter's matcher."""
    matched = expected = fps = 0
    crit_m = crit_e = 0
    for pr_id in list_pr_ids():
        rubric = load_rubric(pr_id)
        exp = rubric["expected_findings"]
        got = pr_findings.get(pr_id, [])
        used: set[int] = set()
        for e in exp:
            expected += 1
            is_crit = e.get("severity") == "critical"
            crit_e += is_crit
            for i, a in enumerate(got):
                if i in used:
                    continue
                if _approximate_match(a, e):
                    used.add(i)
                    matched += 1
                    crit_m += is_crit
                    break
        fps += len(got) - len(used)
    return {"expected": expected, "matched": matched,
            "recall": round(matched / expected, 4) if expected else 0.0,
            "false_positives": fps,
            "critical_expected": crit_e, "critical_matched": crit_m}


def _union(*passes: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Concatenate passes per PR. Dedupe is the SCORER's job (one expected finding can only be
    matched once), which keeps this identical across both arms."""
    out: dict[str, list[dict]] = {}
    for pr_id in list_pr_ids():
        merged: list[dict] = []
        for p in passes:
            merged.extend(p.get(pr_id, []))
        out[pr_id] = merged
    return out


def _load_claude_passes() -> tuple[dict, dict]:
    """reviewer-alone and reviewer+arbiter merged, from pr-arbiter's COMMITTED results."""
    base = json.load(open(os.path.join(ARBITER_REPO, "results", "baseline_20260513.json")))
    it3 = json.load(open(os.path.join(ARBITER_REPO, "results", "iter3_20260513.json")))
    rev = {r["pr_id"]: r.get("reviewer_findings", []) for r in base["per_pr"]}
    merged = {r["pr_id"]: r.get("merged_findings", []) for r in it3["per_pr"]}
    return rev, merged


def report() -> dict:
    claude_rev, claude_role_union = _load_claude_passes()
    qwen_rev: dict[str, list[dict]] = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            qwen_rev = json.load(f)

    res = {
        "single_claude_reviewer": _recall(claude_rev),
        "role_diverse_union_2pass": _recall(claude_role_union),
    }
    if qwen_rev:
        res["single_qwen_reviewer"] = _recall(qwen_rev)
        res["model_diverse_union_2pass"] = _recall(_union(claude_rev, qwen_rev))
    return res


def demo() -> None:
    """Offline self-check: the scorer and the union, on synthetic data. No Ollama, no network."""
    fake_a = {"file": "after.py", "line_range": [10, 12], "category": "security"}
    fake_b = {"file": "after.py", "line_range": [11, 11], "category": "security"}
    fake_c = {"file": "after.py", "line_range": [99, 99], "category": "style"}
    assert _approximate_match(fake_a, fake_b), "±3 line tolerance should match"
    assert not _approximate_match(fake_a, fake_c), "different category must not match"
    u = _union({"pr_001": [fake_a]}, {"pr_001": [fake_c]})
    assert len(u.get("pr_001", [])) == 2, "union concatenates both passes"
    assert _extract_json('```json\n{"findings": []}\n```') == {"findings": []}, "fenced JSON"
    assert _extract_json("garbage no json") == {"findings": []}, "unparseable -> empty, not crash"
    assert _extract_json('prose {"findings": [{"file": "a"}]} tail')["findings"][0]["file"] == "a"
    print("ok — matcher, union, and JSON extraction verified offline")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    elif "--gen" in sys.argv:
        print(f"generating qwen reviewer passes on {len(list_pr_ids())} PRs via {OLLAMA_BASE} ...")
        gen()
        print(json.dumps(report(), indent=2))
    else:
        print(json.dumps(report(), indent=2))
