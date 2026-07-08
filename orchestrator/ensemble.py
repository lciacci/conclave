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


def http_call(base_url: str, model: str, messages: list[dict], timeout: float,
              response_format: dict | None = None) -> str:
    """One OpenAI-compatible chat completion. Returns the message content.
    `response_format={"type": "json_object"}` asks vLLM for guided-JSON output."""
    payload = {"model": model, "messages": messages}
    if response_format:
        payload["response_format"] = response_format
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer none"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"]


def fan_out(query: str, cfg: EnsembleConfig, call=http_call) -> list[dict]:
    """Query every candidate. Records per-model latency (v3 decision 4: this is
    the raw material for the co-resident SM-contention measurement). Sequential
    for now — co-resident models contend for SMs anyway, so parallel client
    threads would not buy real parallelism on one GPU; revisit for multi-GPU."""
    msgs = [{"role": "user", "content": query}]
    out = []
    for model in cfg.candidates:
        t0 = time.monotonic()
        try:
            content = call(cfg.gateway_url, model, msgs, cfg.timeout)
            err = None
        except Exception as e:  # network / model error — keep the others
            content, err = None, f"{type(e).__name__}: {e}"
        out.append({"model": model, "content": content,
                    "latency_s": round(time.monotonic() - t0, 3), "error": err})
    return out


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
        f"Query:\n{query}\n\n" + "\n\n".join(blocks) + "\n\n"
        f"{task}\n"
        'Respond ONLY with JSON: {"chosen": <answer-index or -1 if synthesized>, '
        '"rationale": "<one sentence>", "answer": "<final answer text>"}'
    )
    return [{"role": "system", "content": _JUDGE_SYS}, {"role": "user", "content": user}]


def run_judge(query: str, candidates: list[dict], cfg: EnsembleConfig, call=http_call) -> dict:
    msgs = _judge_messages(query, candidates, cfg.mode)
    t0 = time.monotonic()
    # response_format json_object: vLLM guided decoding returns a pure JSON string,
    # so json.loads parses the whole thing correctly — including braces inside the
    # `answer` field (e.g. code from the coder model). No fragile substring slicing.
    raw = call(cfg.resolved_judge_url(), cfg.judge_model, msgs, cfg.timeout,
               response_format={"type": "json_object"})
    latency = round(time.monotonic() - t0, 3)
    try:
        parsed = json.loads(raw)
        answer = parsed.get("answer") or raw
        rationale = parsed.get("rationale", "")
        chosen = parsed.get("chosen", -1)
    except (ValueError, AttributeError):
        # Backend ignored json_object (older vLLM / non-guided judge) — degrade to
        # the raw text as the answer rather than crash.
        answer, rationale, chosen = raw, "unparsed judge output", -1
    return {"answer": answer, "rationale": rationale, "chosen": chosen,
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
        if messages and messages[0]["role"] == "system":  # judge turn
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
    print("ok — ensemble pipeline + judge parse verified offline")


if __name__ == "__main__":
    demo()
