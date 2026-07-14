#!/usr/bin/env python3
"""Fan the query set out to the three specialists DIRECTLY, no LiteLLM gateway.

WHY THIS EXISTS. The gateway is a convenience, not a requirement: it exists for
per-model cost accounting (v2's goal), and it routes by model name to one of three
vLLM backends. On the RunPod pod, LiteLLM would not install (the image is a Debian
externally-managed Python env, PEP 668), and fighting that while a GPU billed by the
minute was the wrong trade — the gateway buys NOTHING that the headroom measurement
needs.

So we skip it and route by model name here, which is the same job LiteLLM was doing.
This deliberately reuses `candidate_cache.populate()` rather than reimplementing the
fan-out: populate() takes a `call` seam (that is what its demo uses for its offline
self-check), so the cache format, the resume-on-crash behaviour, and the
"all-candidates-errored is NOT cached" guard all stay exactly as they are on the
committed path. Nothing about the experiment changes — only the transport.

Usage (with SSH tunnels to the pod's 8001/8002/8003 open on 18001/18002/18003):
    CONCLAVE_QUERYSET=hard python3 runpod/fanout_direct.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "orchestrator"))
import candidate_cache
from ensemble import EnsembleConfig
from eval_queryset import active_query_set, active_set_name

# model name -> the local port its vLLM server is tunnelled to.
PORTS = {"coder": 18001, "reasoning": 18002, "general": 18003}


def direct_call(base_url, model, messages, timeout, response_format=None, **kw):
    """Route to that model's own vLLM server. Same signature as ensemble.http_call."""
    port = PORTS[model]
    body = {"model": model, "messages": messages, "temperature": 0.0, "max_tokens": 1024}
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


if __name__ == "__main__":
    name = active_set_name()
    qs = active_query_set(name)
    out = candidate_cache.cache_path(name)
    cfg = EnsembleConfig()
    print(f"query set: {name} ({len(qs)} queries x {len(cfg.candidates)} specialists = "
          f"{len(qs) * len(cfg.candidates)} calls)")
    print(f"writing -> {out}")
    cache = candidate_cache.populate(cfg, call=direct_call, path=out, query_set=qs)
    ok = sum(1 for v in cache.values() if all(c.get("content") for c in v))
    print(f"\ndone — {len(cache)}/{len(qs)} queries cached, {ok} with all 3 specialists answering")
    if ok < len(qs):
        print("WARNING: some queries are incomplete — divergence.py will DROP them (never "
              "silently re-base the denominators). Re-run to fill the gaps.")
