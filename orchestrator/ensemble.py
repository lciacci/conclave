#!/usr/bin/env python3
"""Conclave v3 ensemble orchestrator — fan-out to N specialists, judge, return.

Client-side (v3 decision 1): runs on any Tailscale client, calls the LiteLLM
gateway. No GPU needed to iterate the judge — pass a canned `call` fn (see
`demo()`) and exercise the whole pipeline offline against recorded responses.

Wire format is OpenAI /v1/chat/completions (v3 decision 3): stdlib urllib only,
no SDK, no new deps. The gateway, vLLM, and this client all speak it and touch
zero OpenAI servers.

Judge is pluggable (v3 decision 2): `judge_model` + `judge_url` are config. The
default judge is the in-fleet Gemma ("general") — decorrelated from the two Qwen
candidates. Point judge_url/model at a frontier endpoint to run the "beat the
baseline" eval; the orchestrator code does not change.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field


@dataclass
class EnsembleConfig:
    gateway_url: str = "http://localhost:4000"  # set to http://<tailscale-ip>:4000
    candidates: list[str] = field(default_factory=lambda: ["coder", "reasoning", "general"])
    judge_model: str = "general"       # in-fleet Gemma by default
    judge_url: str = ""                # "" => same as gateway_url (in-fleet judge)
    mode: str = "synthesize"           # "synthesize" | "select"
    timeout: float = 120.0

    def resolved_judge_url(self) -> str:
        return self.judge_url or self.gateway_url


def _endpoint(base_url: str) -> str:
    """Resolve the chat-completions URL for an OpenAI-compatible base.

    Most vendors mount the API at the host root, so base + /v1/chat/completions is
    right (OpenAI, Anthropic's compat layer, our own LiteLLM gateway). Gemini does
    NOT: its compat layer lives at /v1beta/openai/chat/completions — the version
    segment comes BEFORE the compat prefix, so blindly appending /v1 gives a 404.

    Rule: if the base already carries a path, the caller has pointed us at the compat
    root themselves and we append only /chat/completions. A bare host gets the /v1."""
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):        # caller passed the full endpoint
        return base
    has_path = urllib.parse.urlparse(base).path.strip("/")
    return f"{base}/chat/completions" if has_path else f"{base}/v1/chat/completions"


def http_call(base_url: str, model: str, messages: list[dict], timeout: float,
              response_format: dict | None = None, api_key: str = "none",
              temperature: float | None = None) -> str:
    """One OpenAI-compatible chat completion. Returns the message content.
    `response_format={"type": "json_object"}` asks vLLM for guided-JSON output.
    `api_key` defaults to "none" for the keyless local gateway; pass a real key
    (e.g. via functools.partial) to reach a frontier endpoint on the same wire
    format — OpenAI, or Anthropic's OpenAI-compatible base_url. That is the seam
    the judge eval uses to run a frontier judge/grader without new code.
    `temperature` is omitted from the request when None (server default); set 0 for
    a deterministic judge/grader (reproducible eval scores)."""
    payload: dict = {"model": model, "messages": messages}
    if response_format:
        payload["response_format"] = response_format
    if temperature is not None:
        payload["temperature"] = temperature
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        _endpoint(base_url),
        data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"]


def _one(model: str, query: str, cfg: EnsembleConfig, call) -> dict:
    msgs = [{"role": "user", "content": query}]
    t0 = time.monotonic()
    try:
        content = call(cfg.gateway_url, model, msgs, cfg.timeout)
        err = None
    except Exception as e:  # network / model error — keep the others
        content, err = None, f"{type(e).__name__}: {e}"
    return {"model": model, "content": content,
            "latency_s": round(time.monotonic() - t0, 3), "error": err}


def fan_out(query: str, cfg: EnsembleConfig, call=http_call) -> list[dict]:
    """Query every candidate one at a time. Each latency_s is therefore a SOLO
    latency — no other model inferencing, no SM contention. max(solo) is the
    ideal-parallel lower bound that fan_out_parallel is measured against."""
    return [_one(m, query, cfg, call) for m in cfg.candidates]


def fan_out_parallel(query: str, cfg: EnsembleConfig, call=http_call) -> tuple[list[dict], float]:
    """Query every candidate concurrently; also return the wall clock. Compare
    that wall against max(solo latency) from fan_out to get the real co-residency
    tax on one GPU (v3 decision 4). Do NOT assume co-residents serialize — vLLM
    processes timeshare the SMs and the size of that tax is the whole question
    Chunk 4's multi-GPU spend rests on. Threads, not processes: these calls are
    blocking urllib I/O, so the GIL is released while the GPU works."""
    from concurrent.futures import ThreadPoolExecutor

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(cfg.candidates)) as ex:
        out = list(ex.map(lambda m: _one(m, query, cfg, call), cfg.candidates))
    return out, round(time.monotonic() - t0, 3)


# Folded into the user turn, not a system message: Gemma-2 (the default judge)
# has no system role in its chat template and 400s on one. A user-embedded
# instruction works across every model in the fleet.
_JUDGE_SYS = (
    "You are a judge over answers from several specialist models to the same "
    "query. You do not see which model produced which answer beyond a label. "
    "Reason about correctness and completeness, not style."
)


def _judge_messages(query: str, candidates: list[dict], mode: str) -> list[dict]:
    blocks = []
    for i, c in enumerate(candidates):
        if c["content"] is None:
            continue
        blocks.append(f"[Answer {i} — {c['model']}]\n{c['content']}")
    task = (
        "Synthesize the single best answer, merging correct points and dropping errors."
        if mode == "synthesize"
        else "Select the single best answer verbatim."
    )
    user = (
        f"{_JUDGE_SYS}\n\n"
        f"Query:\n{query}\n\n" + "\n\n".join(blocks) + "\n\n"
        f"{task}\n"
        'Respond ONLY with JSON: {"chosen": <answer-index or -1 if synthesized>, '
        '"rationale": "<one sentence>", "answer": "<final answer text>"}'
    )
    return [{"role": "user", "content": user}]


def run_judge(query: str, candidates: list[dict], cfg: EnsembleConfig, call=http_call) -> dict:
    msgs = _judge_messages(query, candidates, cfg.mode)
    t0 = time.monotonic()
    try:
        # response_format json_object: vLLM guided decoding returns a pure JSON
        # string, so json.loads parses the whole thing correctly — including braces
        # inside the `answer` field (e.g. code). No fragile substring slicing.
        raw = call(cfg.resolved_judge_url(), cfg.judge_model, msgs, cfg.timeout,
                   response_format={"type": "json_object"})
    except Exception as e:
        # Judge unreachable / rejected the request — degrade to the first valid
        # candidate rather than sink the whole ensemble (fan-out does the same).
        #
        # `error` is a STRUCTURED flag, and callers must branch on it, never on the
        # wording of `rationale`. The returned `answer` here is NOT a judgment — it is
        # some specialist's raw reply. That is right for serving (return something) and
        # catastrophic for an eval (grading a candidate as if it were the judge's
        # output). judge_eval keys off `error` to refuse to cache these; a caller that
        # string-matched the rationale text would break the moment this message is
        # reworded, silently restoring that bug.
        latency = round(time.monotonic() - t0, 3)
        fallback = next((c["content"] for c in candidates if c["content"]), None)
        return {"answer": fallback, "rationale": f"judge failed: {type(e).__name__}: {e}",
                "error": f"{type(e).__name__}: {e}", "chosen": -1,
                "model": cfg.judge_model, "latency_s": latency}
    latency = round(time.monotonic() - t0, 3)
    try:
        parsed = json.loads(raw)
        answer = parsed.get("answer") or raw
        rationale = parsed.get("rationale", "")
        chosen = parsed.get("chosen", -1)
    except (ValueError, AttributeError):
        # Backend ignored json_object (older vLLM / non-guided judge) — degrade to
        # the raw text as the answer rather than crash. NOT an error: the judge did
        # reply, and the raw reply IS its answer, merely unstructured. So this row is
        # a real (if degraded) judgment and is safe to cache.
        answer, rationale, chosen = raw, "unparsed judge output", -1
    return {"answer": answer, "rationale": rationale, "chosen": chosen, "error": None,
            "model": cfg.judge_model, "latency_s": latency}


def ensemble(query: str, cfg: EnsembleConfig, call=http_call) -> dict:
    """Full pipeline. Returns an OpenAI-compatible completion whose message is the
    judged answer, with candidates + judge detail in `metadata` (v3 decision 3)."""
    t0 = time.monotonic()
    candidates = fan_out(query, cfg, call)
    judge = run_judge(query, candidates, cfg, call)
    return {
        "object": "chat.completion",
        "model": "ensemble",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": judge["answer"]}}],
        "metadata": {
            "candidates": candidates,
            "judge": judge,
            "wall_s": round(time.monotonic() - t0, 3),
        },
    }


def demo() -> None:
    """Offline self-check — no network, canned call fn. Proves the pipeline shape
    and judge JSON parsing without a GPU boot (v3 decision 1)."""
    canned = {
        "coder": '`sorted(xs)` returns a new sorted list.',
        "reasoning": "Use sorted(xs); it does not mutate the input.",
        "general": "You can sort with sorted().",
        # judge is model "general"; the LAST call in the pipeline is the judge,
        # so route by message shape: judge messages carry the system prompt.
    }

    # Judge answer deliberately contains braces (a dict literal) — proves json.loads
    # over guided-JSON output survives braces-in-strings, the bug the old slicer had.
    judge_json = json.dumps({
        "chosen": 1,
        "rationale": "most precise",
        "answer": "Use sorted(xs); for keys pass sorted(xs, key=lambda d: d['k']).",
    })

    def fake_call(base_url, model, messages, timeout, response_format=None):
        # Judge turn is the one carrying the JSON instruction (no system role —
        # folded into the user message so Gemma accepts it).
        if "Respond ONLY with JSON" in messages[-1]["content"]:
            assert response_format == {"type": "json_object"}, "judge asks for JSON mode"
            return judge_json
        return canned[model]

    cfg = EnsembleConfig()
    res = ensemble("How do I sort a list in Python without mutating it?", cfg, call=fake_call)

    assert res["model"] == "ensemble"
    assert len(res["metadata"]["candidates"]) == 3, "all candidates recorded"
    assert all("latency_s" in c for c in res["metadata"]["candidates"]), "per-model timing"
    assert res["metadata"]["judge"]["chosen"] == 1, "judge JSON parsed"
    content = res["choices"][0]["message"]["content"]
    assert "sorted(xs)" in content and "d['k']" in content, "braces-in-answer survived parse"
    assert res["metadata"]["wall_s"] >= 0

    # Parallel fan-out: same shape/order as sequential, and it really overlaps —
    # 3 x 0.2s sleeps must finish well under the 0.6s a serial pass would take.
    def slow_call(base_url, model, messages, timeout, response_format=None):
        time.sleep(0.2)
        return canned[model]

    par, wall = fan_out_parallel("q", cfg, call=slow_call)
    assert [c["model"] for c in par] == cfg.candidates, "order preserved"
    assert all(c["error"] is None for c in par), "no errors on the happy path"
    assert wall < 0.5, f"threads did not overlap: wall={wall}s"

    # Endpoint resolution — a bare host takes /v1; a base that already carries the
    # compat path does not (Gemini mounts at /v1beta/openai, so /v1 would 404).
    assert _endpoint("http://localhost:4000") == "http://localhost:4000/v1/chat/completions"
    assert _endpoint("https://api.anthropic.com") == "https://api.anthropic.com/v1/chat/completions"
    assert _endpoint("https://generativelanguage.googleapis.com/v1beta/openai") == \
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert _endpoint("https://x.com/v1/chat/completions") == "https://x.com/v1/chat/completions"
    print("ok — ensemble pipeline + judge parse + parallel fan-out + endpoint resolution verified offline")


if __name__ == "__main__":
    demo()
