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

    python3 orchestrator/s2_model_axis.py --gen     # generate local reviewer passes (local, $0)
    python3 orchestrator/s2_model_axis.py           # score whatever exists
    python3 orchestrator/s2_model_axis.py --demo    # offline self-check, no Ollama

$LOCAL_CODER selects which local model plays the second-model arm (default qwen3-coder:30b) and
picks the passes file, so a re-run on a newer open-weight model neither overwrites nor resumes into
the committed qwen passes. Both then score against the same claude arms.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

ARBITER_REPO = os.environ.get("PR_ARBITER_REPO",
                             os.path.join(os.path.dirname(HERE), "..", "pr-arbiter"))
ARBITER_REPO = os.path.abspath(ARBITER_REPO)

OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://localhost:11434")
MODEL = os.environ.get("LOCAL_CODER", "qwen3-coder:30b")


def _passes_path(model: str) -> str:
    """One passes file PER MODEL — the cache is keyed by pr_id only, so a shared file would let a
    second model resume into the first one's rows and silently score a blend of the two.

    qwen keeps its historical filename so the committed 2026-07-28 passes still load.
    """
    override = os.environ.get("S2_PASSES_FILE")
    if override:                     # control arms: same model, one knob changed (e.g. max_tokens)
        # Bare filename only. The module docstring promises "writes only into conclave", and
        # os.path.join returns an absolute override unchanged, so `../../pr-arbiter/results/...`
        # would have gen() overwrite the reference data this script scores against.
        if os.path.basename(override) != override:
            raise SystemExit(f"$S2_PASSES_FILE must be a bare filename, got: {override!r}")
        return os.path.join(HERE, override)
    # Normalised compare, so `Qwen3-Coder:30B` resumes the historical file instead of silently
    # regenerating it for ~40 min under a new name.
    norm = model.strip().lower()
    if norm == "qwen3-coder:30b":
        return os.path.join(HERE, "s2_model_axis_qwen_passes.json")
    # The slug is lossy (`foo:30b` and `foo-30b` collide), and a collision reintroduces exactly the
    # cross-model blend this function exists to prevent — so disambiguate with a digest.
    slug = re.sub(r"[^a-z0-9]+", "-", norm).strip("-")
    digest = hashlib.sha256(norm.encode()).hexdigest()[:8]
    return os.path.join(HERE, f"s2_model_axis_{slug}-{digest}_passes.json")


OUT = _passes_path(MODEL)
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


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a model reply. Weak models fence or preamble.

    Returns None when NO object could be parsed — do not conflate that with `{"findings": []}`.
    An unparseable reply and an honest "this diff is clean" both score as zero findings, and
    telling them apart is the whole point: the first is a harness artifact, the second is data.
    This function used to return `{"findings": []}` on every failure path, which made the
    distinction unrecoverable by the caller.
    """
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
        return None
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
                    return None
    return None                        # ran off the end: object never closed = truncated mid-object


def gen() -> dict[str, list[dict]]:
    """Generate $LOCAL_CODER reviewer passes over the corpus. Resumes; incremental writes."""
    from ensemble import http_call

    cache: dict = {}
    starved: list[str] = []
    if os.path.exists(OUT):
        with open(OUT) as f:
            cache = json.load(f)

    # Provenance travels WITH the passes, not with the environment that happens to be set when
    # they are scored: $S2_PASSES_FILE selects the file independently of $LOCAL_CODER, so reporting
    # the env var mislabels which model produced a number — the exact error class being retracted.
    # `_meta` is not a pr_id, and every consumer iterates list_pr_ids(), so it is inert to scoring.
    prior = cache.get("_meta")
    if prior and (prior.get("model") != MODEL or prior.get("max_tokens") != MAX_TOKENS):
        print(f"!! {os.path.basename(OUT)} was generated by model={prior.get('model')} at "
              f"max_tokens={prior.get('max_tokens')}; this run is model={MODEL} at "
              f"max_tokens={MAX_TOKENS}. Resuming would BLEND two arms into one file.\n"
              f"   Use $S2_PASSES_FILE to write a separate file, or delete this one.",
              file=sys.stderr)
        raise SystemExit(2)
    cache["_meta"] = {"model": MODEL, "max_tokens": MAX_TOKENS}

    generated = 0
    for pr_id in list_pr_ids():
        if pr_id in cache:
            continue
        generated += 1
        inp = load_agent_input(pr_id)          # NEVER load_rubric here — that would leak the answers
        user = (f"Review this pull request.\n\nDIFF:\n```\n{inp['diff']}\n```\n\n"
                f"AFTER STATE ({inp['lang']}):\n```{inp['lang']}\n{inp['after']}\n```")
        msgs = [{"role": "system", "content": _reviewer_system() + JSON_INSTRUCTION},
                {"role": "user", "content": user}]
        t0 = time.monotonic()
        try:
            raw = http_call(OLLAMA_BASE, MODEL, msgs, TIMEOUT, max_tokens=MAX_TOKENS)
            parsed = _extract_json(raw)
        except Exception as e:                                   # noqa: BLE001
            print(f"  {pr_id}: ERROR {type(e).__name__}: {e}", file=sys.stderr)
            continue

        # A REASONING model spends $MAX_TOKENS on reasoning BEFORE it emits content, so a tight
        # budget yields an empty or mid-object-truncated reply. Either way there are no findings to
        # score — and a naive zero there is indistinguishable from an honest "this diff is clean",
        # which is this repo's oldest mistake: grading a harness artifact as a model property.
        #
        # Both unscoreable shapes are caught here, because BOTH occur: `parsed is None` covers no
        # object and truncated-mid-object (the likelier shape at a tight budget, and the one an
        # earlier `"{" not in raw` version of this check silently missed); a parsed object with no
        # `findings` key means the model answered but ignored the schema, which is equally
        # uninterpretable as a zero.
        #
        # This WARNS, it does not refuse — a genuinely clean corpus would trip a hard refusal too.
        if parsed is None:
            starved.append(pr_id)
            print(f"  {pr_id}: NO PARSEABLE JSON — unscoreable, not a finding", file=sys.stderr)
        elif "findings" not in parsed:
            starved.append(pr_id)
            print(f"  {pr_id}: JSON WITHOUT 'findings' KEY — unscoreable, not a finding",
                  file=sys.stderr)

        findings = (parsed or {}).get("findings", []) or []
        cache[pr_id] = findings
        with open(OUT, "w") as f:
            json.dump(cache, f, indent=2)
        print(f"  {pr_id}: {len(findings)} findings, {time.monotonic() - t0:.1f}s")

    if starved:
        # Denominator is what THIS run generated, not the corpus — on a resume most rows are
        # cached and a corpus-wide denominator understates the starvation rate of the new work.
        print(f"\n!! {len(starved)}/{generated} PRs generated this run were UNSCOREABLE "
              f"at max_tokens={MAX_TOKENS}: {', '.join(starved)}\n"
              f"   They are cached as zero findings and will NOT be retried on resume — "
              f"`pr_id in cache` skips them and this warning will not fire again.\n"
              f"   Raise $CONCLAVE_MAX_TOKENS, delete those keys from {os.path.basename(OUT)}, "
              f"re-run, and do NOT quote a recall until this is clean.",
              file=sys.stderr)
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
    local_rev: dict[str, list[dict]] = {}
    if os.path.exists(OUT):
        with open(OUT) as f:
            local_rev = json.load(f)

    meta = local_rev.get("_meta") if local_rev else None
    res: dict = {
        # From the passes file when present, NOT from $LOCAL_CODER — see the note in gen().
        # Files generated before `_meta` existed report the env var, marked as unverified.
        "local_model": (meta or {}).get("model", f"{MODEL} (unverified: pre-_meta passes file)"),
        "local_max_tokens": (meta or {}).get("max_tokens"),
        "single_claude_reviewer": _recall(claude_rev),
        "role_diverse_union_2pass": _recall(claude_role_union),
    }
    if local_rev:
        res["single_local_reviewer"] = _recall(local_rev)
        res["model_diverse_union_2pass"] = _recall(_union(claude_rev, local_rev))
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
    assert _extract_json('prose {"findings": [{"file": "a"}]} tail')["findings"][0]["file"] == "a"

    # The distinction the scoring depends on: an honest empty result is a dict, every unscoreable
    # shape is None. Conflating them is how a starved run gets graded as under-detection.
    assert _extract_json('{"findings": []}') == {"findings": []}, "honest zero is NOT None"
    assert _extract_json("garbage no json") is None, "no object -> None"
    assert _extract_json('{"findings": [{"file": "a.py", "categ') is None, "truncated mid-object"
    assert _extract_json("{not json at all}") is None, "brace present but unparseable"
    assert _extract_json("") is None, "empty reply"

    # Path derivation: the override is confined to conclave, and near-miss model tags do not collide.
    os.environ.pop("S2_PASSES_FILE", None)
    assert _passes_path("Qwen3-Coder:30B") == _passes_path("qwen3-coder:30b"), "case-insensitive"
    assert _passes_path("foo:30b") != _passes_path("foo-30b"), "slug collision must not alias"
    try:
        os.environ["S2_PASSES_FILE"] = "../../pr-arbiter/results/baseline_20260513.json"
        _passes_path("x")
        raise AssertionError("traversal in $S2_PASSES_FILE must be rejected")
    except SystemExit:
        pass
    finally:
        os.environ.pop("S2_PASSES_FILE", None)
    print("ok — matcher, union, JSON extraction, and path derivation verified offline")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    elif "--gen" in sys.argv:
        print(f"generating {MODEL} reviewer passes on {len(list_pr_ids())} PRs "
              f"via {OLLAMA_BASE} -> {os.path.basename(OUT)} ...")
        gen()
        print(json.dumps(report(), indent=2))
    else:
        print(json.dumps(report(), indent=2))
