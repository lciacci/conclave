#!/usr/bin/env python3
"""Candidate response cache for the judge eval (Chunk 3).

v3 decision 1: iterate judge/scorer logic with the GPU box DOWN. This is the seam
that makes that real — one boot fans every query out to the fleet and writes the
candidate responses to disk; every eval run after that reads the cache and pays
for zero GPU time. Re-run the judge prompt, swap judges, retune the scorer — all
offline against the same frozen candidates.

Populate (needs the fleet up):
    CONCLAVE_GW=<ts-ip>:4000 python3 orchestrator/candidate_cache.py
Then judge_eval.py reads eval_candidates.json with the box torn down.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ensemble import EnsembleConfig, fan_out, http_call
from eval_queryset import QUERY_SET

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PATH = os.path.join(_HERE, "eval_candidates.json")
FIXTURE_PATH = os.path.join(_HERE, "eval_fixtures", "eval_candidates.json")


def load(path: str = DEFAULT_PATH, allow_fixture: bool = True) -> dict[str, list[dict]]:
    """Live cache if present, else (for READERS) the committed fixture. The live file is
    gitignored, so without the fallback a fresh clone finds no candidates and --score
    cannot replay the published run at all.

    `allow_fixture=False` is for WRITERS (populate). A generator must never treat the
    read-only fixture as its own resume state: it would boot the GPU, make ZERO fan-out
    calls, and silently adopt the frozen candidates as if this fleet had produced them —
    paying for a box and generating nothing."""
    if not os.path.exists(path) and allow_fixture and os.path.exists(FIXTURE_PATH):
        path = FIXTURE_PATH
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def save(cache: dict[str, list[dict]], path: str = DEFAULT_PATH) -> None:
    with open(path, "w") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def populate(cfg: EnsembleConfig, call=http_call, path: str = DEFAULT_PATH,
             query_set: list[dict] = QUERY_SET, refresh: bool = False) -> dict[str, list[dict]]:
    """Fan each query out to the fleet, caching candidates by query id. Skips ids
    already cached unless refresh=True, so a re-run after a crash resumes cheaply.
    A candidate set with every content None (whole fleet errored) is NOT cached —
    so a transient gateway blip doesn't freeze bad data into the cache."""
    # allow_fixture=False: resume only from OUR OWN live cache, never from the committed
    # fixture. Otherwise a fresh clone's --generate would boot the box, adopt the fixture's
    # 36 candidate sets as already-done, and call the fleet zero times.
    cache = {} if refresh else load(path, allow_fixture=False)
    for q in query_set:
        if q["id"] in cache and not refresh:
            continue
        cands = fan_out(q["prompt"], cfg, call)
        if all(c["content"] is None for c in cands):
            print(f"  {q['id']}: all candidates errored — not caching", file=sys.stderr)
            continue
        cache[q["id"]] = cands
        save(cache, path)  # persist incrementally: a mid-run crash keeps progress
        print(f"  cached {q['id']} ({sum(c['content'] is not None for c in cands)}/{len(cands)} ok)")
    return cache


def demo() -> None:
    """Offline self-check — populate + load round-trips with a canned fleet, no GPU."""
    import tempfile

    def fake_call(base_url, model, messages, timeout, response_format=None):
        return f"{model}'s answer to: {messages[-1]['content'][:20]}"

    tmp = os.path.join(tempfile.mkdtemp(), "cand.json")
    cfg = EnsembleConfig()
    small = QUERY_SET[:3]
    cache = populate(cfg, call=fake_call, path=tmp, query_set=small)
    assert len(cache) == 3, "all three cached"
    reloaded = load(tmp)
    assert reloaded.keys() == cache.keys()
    first = small[0]["id"]
    assert len(reloaded[first]) == len(cfg.candidates), "one candidate per specialist"
    assert all(c["content"] for c in reloaded[first]), "canned content present"

    # a query whose fleet all-errors must NOT be cached
    def dead_call(base_url, model, messages, timeout, response_format=None):
        raise ConnectionError("gateway down")
    tmp2 = os.path.join(tempfile.mkdtemp(), "cand2.json")
    cache2 = populate(cfg, call=dead_call, path=tmp2, query_set=small)
    assert cache2 == {}, "all-errored queries are not frozen into the cache"
    print("ok — candidate cache populate/load + all-error guard verified offline")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        gw = os.environ.get("CONCLAVE_GW")
        if not gw:
            sys.exit("set CONCLAVE_GW=<ts-ip>:4000 to populate against the live fleet "
                     "(or pass --demo for the offline self-check)")
        cfg = EnsembleConfig(gateway_url=gw if gw.startswith("http") else f"http://{gw}")
        print(f"populating candidate cache from {cfg.gateway_url} ...")
        cache = populate(cfg)
        print(f"done — {len(cache)} queries cached to {DEFAULT_PATH}")
