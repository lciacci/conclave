#!/usr/bin/env python3
"""v3 live harness — smoke + contention baseline against a running fleet.

Gateway comes from argv[1] or $CONCLAVE_GW (the Tailscale IP changes every boot):
    CONCLAVE_GW=100.x.y.z:4000 python3 orchestrator/harness.py
    python3 orchestrator/harness.py 100.x.y.z:4000 --baseline

Modes:
    (default)    one ensemble query end-to-end — the Chunk 1 smoke check
    --baseline   Chunk 2 contention baseline: seq fan-out wall vs slowest model
                 (= ideal-parallel lower bound); the gap is the co-residency tax.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ensemble import EnsembleConfig, ensemble, fan_out


def _gateway() -> str:
    for a in sys.argv[1:]:
        if not a.startswith("-"):
            return a if a.startswith("http") else f"http://{a}"
    gw = os.environ.get("CONCLAVE_GW")
    if not gw:
        sys.exit("gateway required: argv[1] or $CONCLAVE_GW (e.g. 100.x.y.z:4000)")
    return gw if gw.startswith("http") else f"http://{gw}"


def smoke(cfg: EnsembleConfig) -> None:
    q = ("Write a Python one-liner to return the second-largest unique value in "
         "a list. Explain in one sentence.")
    res = ensemble(q, cfg)
    j = res["metadata"]["judge"]
    print("=== JUDGED ANSWER (judge=%s) ===" % j["model"])
    print(res["choices"][0]["message"]["content"][:600])
    print("\nchosen:", j["chosen"], "| rationale:", j["rationale"],
          "| judge_latency_s:", j["latency_s"])
    print("\n=== CANDIDATES ===")
    for c in res["metadata"]["candidates"]:
        tag = "ERR: " + str(c["error"]) if c["error"] else c["content"][:70].replace("\n", " ")
        print("%-9s %6.2fs  %s" % (c["model"], c["latency_s"], tag))
    print("\nwall_s:", res["metadata"]["wall_s"])


def baseline(cfg: EnsembleConfig) -> None:
    queries = [
        "Explain what a race condition is in two sentences.",
        "Write a Python function to check if a string is a palindrome.",
        "What is the time complexity of binary search and why?",
        "Summarize the CAP theorem in three bullet points.",
    ]
    seqs, ideals = [], []
    for q in queries:
        lats = {c["model"]: c["latency_s"] for c in fan_out(q, cfg)}
        seq, ideal = sum(lats.values()), max(lats.values())
        seqs.append(seq)
        ideals.append(ideal)
        tax = round((seq - ideal) / ideal * 100)
        print("seq=%6.2fs  ideal(parallel)=%6.2fs  tax=+%d%%   %s"
              % (seq, ideal, tax, {k: round(v, 1) for k, v in lats.items()}))
    n = len(seqs)
    ms, mi = sum(seqs) / n, sum(ideals) / n
    print("\nMEAN over %d: seq=%.2fs  ideal=%.2fs  serialization tax=+%d%%"
          % (n, ms, mi, round((ms - mi) / mi * 100)))
    print("NB: a valid run needs a healthy box — suspend/relax idle-stop first "
          "(dev_mode) or it may reap mid-run (uniform ~75s = dying box).")


if __name__ == "__main__":
    cfg = EnsembleConfig(gateway_url=_gateway())
    (baseline if "--baseline" in sys.argv else smoke)(cfg)
