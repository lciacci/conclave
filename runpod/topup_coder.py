#!/usr/bin/env python3
"""Top up ONE specialist's answers in an existing candidate cache.

WHY. `coder` failed on every query of the hard-set fan-out with HTTP 405 — RunPod's own
proxy squats on port 8001, so coder's vLLM never bound ("Address already in use") while
8002/8003 came up fine. All 30 queries cached with 2 of 3 specialists.

`candidate_cache.populate()` cannot fix this: it resumes by skipping query ids already
present, so a re-run would skip all 30. `refresh=True` would work but re-runs ALL 90
calls and, worse, would regenerate `reasoning` and `general` answers that are already
good — pointlessly re-rolling two thirds of the experiment on a billing GPU.

So: call ONLY the missing model, for the ids that lack it, and merge in place. The other
two specialists' answers are untouched, which is what we want — they are the same
answers the ensemble would have seen.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "orchestrator"))
import candidate_cache
from eval_queryset import active_query_set, active_set_name

MODEL = "coder"
PORT = 18011   # coder was moved off 8001 (RunPod proxy owns it) to 8011


def call(prompt: str) -> str:
    body = {"model": MODEL, "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0, "max_tokens": 1024}
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


if __name__ == "__main__":
    name = active_set_name()
    path = candidate_cache.cache_path(name)
    qs = {q["id"]: q for q in active_query_set(name)}
    cache = json.load(open(path))

    todo = [qid for qid, cands in cache.items()
            if any(c["model"] == MODEL and not c.get("content") for c in cands)]
    print(f"{len(todo)} queries missing '{MODEL}' — topping up")

    for i, qid in enumerate(todo, 1):
        try:
            content = call(qs[qid]["prompt"])
            for c in cache[qid]:
                if c["model"] == MODEL:
                    c["content"], c["error"] = content, None
            json.dump(cache, open(path, "w"), indent=2, ensure_ascii=False)  # persist each
            print(f"  [{i}/{len(todo)}] {qid}  ({len(content)} chars)")
        except Exception as e:
            print(f"  [{i}/{len(todo)}] {qid}  FAILED: {e}")

    complete = sum(1 for v in cache.values() if all(c.get("content") for c in v))
    print(f"\n{complete}/{len(cache)} queries now have all 3 specialists")
